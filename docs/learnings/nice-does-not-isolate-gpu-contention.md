# `nice -n 19` 不隔离 GPU —— 保护训练只能靠挂起或杀

日期: 2026-07-16
触发: 决定性 A/B(owner_v7_holdout)在 M4 Air 上只拿到 33-61% CPU,
      打标包生成跑了 12 小时才完成 1500/3600。

## 病因

2026-07-15 启动 round6 打标包生成时,我用了:

    nohup nice -n 19 env PYTHONPATH=. .venv/bin/python scripts/make_round6_packs.py &

并对项目所有者声称"低优先级不抢 A/B 的 GPU"。**这句话是错的。**

`nice` 只调整**内核 CPU 调度器**的时间片权重。它对以下三样东西完全无效:

1. **MPS 的 GPU 命令队列** —— Metal 按提交顺序排队,不看提交者的 nice 值。
   两个进程各自提交 YOLO 推理,GPU 就在两者间来回切,训练的 kernel
   被夹在打分的 kernel 中间等。
2. **统一内存带宽** —— M4 Air 的 CPU/GPU 共享 ~120GB/s。nice 不分配带宽。
3. **PyTorch 的 CPU 线程池** —— 数据加载/预处理的 worker 线程继承 nice 值,
   但它们抢的是内存带宽,不是 CPU 时间片。

结果:三个 python 进程(打标包生成 + promote_owner_best + visual_scout)
合计吃掉 ~300% CPU,把训练压到 33%。训练速度 1.4-1.5 s/it。

## 处置

把竞争者停掉后,同一个训练立刻变成 **1.1 s/it(快 20-25%)**,且这只是
CPU 侧的收益 —— GPU 队列不再被打断的收益无法单独测量,但方向一致。

正确做法,按侵入性从低到高:

    kill -STOP <pid>    # 挂起:进度全保留,可 kill -CONT 恢复。首选。
    kill -TERM <pid>    # 让它自己收尾
    kill -9 <pid>       # 最后手段

**挂起(SIGSTOP)是默认答案**:它立刻释放 CPU 和 GPU 队列,又不销毁进度。
在 16GB 的机器上它还不释放内存(进程还在),但内存不是本次的瓶颈。

## 更深的一条

**在单 GPU 机器上,"后台低优先级跑个别的"这个念头本身就是错的。**
Mac 只有一块 GPU,没有 MIG、没有时间片隔离、没有显存分区。想让训练跑得
快,机器就得归它一个人。任何"顺便跑一下"都是在偷训练的时间。

这直接推出了换机结论:**训练该搬到 RTX 3060**(CUDA + 12GB 独占显存),
Mac 只留给数据/回测/看板这些真正能并行的活。见 `~/Desktop/fable-3060/`。

## 附带教训:grep 关键词漏了就会误报"进程死了"

排查时我用 `ps aux | grep -E "ab_decisive|make_round6|ultralytics|yolo"`
找训练进程,一无所获,差点向项目所有者报告"A/B 训练已死"。

实际命令行是 `python -m src.detection.train` —— **不含 "yolo" 或
"ultralytics" 字样**,被我自己的 grep 漏掉了。

判定进程死活,不要靠 grep 猜关键词,要靠:

    pgrep -P <父进程pid>              # 看子进程树,不依赖命令行长相
    wc -c < log; sleep 5; wc -c < log # 日志还在长 = 活的

同一个坑上个月刚踩过一次(`sort -t, -k8 -rn` 把 `2e-05` 读成 2,差点
把正常训练报成故障)。**报告故障前先证明故障存在** —— 这是铁律级别的纪律。
