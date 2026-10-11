import json
from typer.testing import CliRunner
from socialctl.cli import app
from tests.test_community_batches import prepared


def test_full_batch_preview_and_no_blanket_yes(tmp_path,monkeypatch):
    batches,mod,brand,graph,store,children,refs=prepared(tmp_path,monkeypatch)
    import yaml
    (brand.raiz/'accounts.yml').write_text(yaml.safe_dump(brand.cuentas))
    file=tmp_path/'batch.json';file.write_text(json.dumps({'version':1,'children':refs}))
    runner=CliRunner();common=['--brand',brand.nombre,'--root',str(tmp_path)]
    result=runner.invoke(app,['changes','prepare-batch','--file',str(file),'--dry-run',*common])
    assert result.exit_code==0,result.output
    assert all(c.text in result.output for c in children)
    batch=batches.CommunityBatchStore(brand.raiz).load(next(batches.CommunityBatchStore(brand.raiz).root.glob('*.json')).stem)
    rejected=runner.invoke(app,['changes','apply-batch',batch.id,'--yes',*common])
    assert rejected.exit_code!=0 and not graph.writes
