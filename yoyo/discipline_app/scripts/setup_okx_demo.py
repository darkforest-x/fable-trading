"""Private terminal entry for independent OKX demo credentials; read-only probe."""
import getpass
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from yoyo.discipline_app.okx_demo import Credentials, DemoProbe, DemoError, save_private


def main():
    if not sys.stdin.isatty():
        raise SystemExit('请在 Mac 终端交互运行，凭据不接受命令行参数或管道。')
    os.umask(0o077)
    runtime = Path.home() / 'Library/Application Support/SpikeDiscipline'
    destination = runtime / 'okx-demo-credentials.json'
    print('Spike · OKX 模拟账户接入核验\n仅支持 www.okx.com 站点；所有请求固定为模拟环境。')
    print('只读取账户，不下单、不改账户模式。三项输入都不回显，粘贴后按回车。')
    print('核验通过后保存到本机独立私有目录；App 访问令牌不是这里的 API Key。\n')
    if destination.exists() and input('已有此 App 专用凭据。输入 REPLACE 才替换：').strip() != 'REPLACE':
        return
    probe = None
    try:
        credentials = Credentials(getpass.getpass('OKX 模拟 API Key：').strip(),
            getpass.getpass('OKX 模拟 Secret：').strip(), getpass.getpass('API Passphrase：'))
        probe = DemoProbe(credentials)
        status = probe.inspect()
        save_private(destination, dict(environment='okx_demo', host='https://www.okx.com',
            key=credentials.key, secret=credentials.secret, passphrase=credentials.passphrase))
        save_private(runtime / 'okx-demo-status.json', status)
        print('\n模拟 API 核验成功，凭据已安全保存在本机。')
        print('OKX 下单尚未启用；接下来核对成交与保护单流程。')
    except (DemoError, KeyboardInterrupt, EOFError) as exc:
        print('\n' + (str(exc) if isinstance(exc, DemoError) else '已取消，没有保存本次输入。'))
    finally:
        if probe:
            probe.close()
    input('\n按回车关闭…')


if __name__ == '__main__':
    main()
