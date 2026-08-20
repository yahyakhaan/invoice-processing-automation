from main import main
from invoice_agent.config import ROOT


def test_hitl_demo_cli(tmp_path, monkeypatch, settings, capsys):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("INVENTORY_DB", str(settings.inventory_db))
    monkeypatch.setenv("CHECKPOINT_DB", str(settings.checkpoint_db))
    code = main(["--demo=vp-review", "--provider=mock", "--output", str(tmp_path / "out")])
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert code == 0
    assert "PENDING" in combined or "thread_id" in combined
    assert "--resume=" in combined
    assert "--vp-decision=approve" in combined
