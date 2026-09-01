"""Static safety contract for the offline 15m L2 RTX 3060 runner."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts/run_15m_ma_launch_l2_global_context_on_3060.sh"
POWERSHELL = ROOT / "scripts/windows/run_l2_global_context_scan.ps1"
REPORT_BUILDER = ROOT / "scripts/build_15m_ma_launch_l2_global_context_report.py"


def test_collector_requires_both_success_marker_and_terminal_receipt() -> None:
    text = SHELL.read_text(encoding="utf-8")
    assert "scan has not written scan.exit" in text
    assert "terminal scan receipt is missing" in text
    assert '[[ "$exit_code" == "0" ]]' in text
    assert 'receipt["symbols"] == 54' in text
    assert 'receipt["holdout_rows_read"] == 0' in text
    assert 'receipt["network_reads"] == 0' in text


def test_worker_is_inference_only_and_requires_atomic_terminal_receipt() -> None:
    text = POWERSHELL.read_text(encoding="utf-8")
    assert "--scan" in text
    assert "--train-evaluate" not in text
    assert "--build-dataset" not in text
    assert "scanner exited zero without terminal receipt" in text
    assert "$code = 97" in text


def test_shell_and_powershell_pin_the_same_remote_root() -> None:
    shell = SHELL.read_text(encoding="utf-8")
    powershell = POWERSHELL.read_text(encoding="utf-8")
    root = "C:/fable/l2_exp-15m-ma-launch-global-context-v1"
    assert f'REMOTE="{root}"' in shell
    assert root in powershell


def test_status_finds_the_actual_python_workload_not_only_launcher_pid() -> None:
    text = SHELL.read_text(encoding="utf-8")
    status = text.split("status() {", 1)[1].split("verify_collected() {", 1)[0]
    assert "Get-CimInstance Win32_Process" in status
    assert "Name -like 'python*'" in status
    assert "research_15m_ma_launch_l2_global_context.py" in status
    assert "workload_running=" in status
    assert "worker_cpu_seconds=" in status
    assert "workload_identity=fallback_script_only" in status
    assert "launcher_pid_record=" in status
    assert "scan.source_commit" in text


def test_collected_archive_excludes_snapshot_weights_and_execution_state() -> None:
    text = SHELL.read_text(encoding="utf-8")
    collect = text.split("collect() {", 1)[1].split("case \"$MODE\"", 1)[0]
    assert "scan_by_symbol" in collect
    assert "accepted_candidates.csv" in collect
    assert "episodes.csv" in collect
    assert "best.pt" not in collect
    assert "/snapshot" not in collect
    assert "forward_log" not in collect
    assert "ACTIVE" not in collect


def test_report_reproduction_uses_the_fail_closed_remote_runner() -> None:
    text = REPORT_BUILDER.read_text(encoding="utf-8")
    runner = "scripts/run_15m_ma_launch_l2_global_context_on_3060.sh"
    assert f"bash {runner} --stage" in text
    assert f"bash {runner} --start" in text
    assert f"bash {runner} --collect" in text
