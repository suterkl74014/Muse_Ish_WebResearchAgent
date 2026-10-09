from pathlib import Path
from tempfile import TemporaryDirectory
import json, zipfile
from webagent.diagnostics import DiagnosticManager


def test_diagnostics_redacts_and_bundles():
    with TemporaryDirectory() as d:
        root=Path(d); dm=DiagnosticManager(root/'diag'); dm.start_run('test',1,{'api_key':'secret-value'})
        dm.log('provider_error',message='Authorization: Bearer ABC123')
        text=Path(dm.current_paths()['jsonl']).read_text()
        assert 'secret-value' not in text
        assert 'ABC123' not in text
        db=root/'db.sqlite'; db.write_bytes(b'test')
        cfg=root/'config.json'; cfg.write_text(json.dumps({'token':'hidden','mode':'hybrid'}))
        bundle=dm.bundle(db,cfg)
        assert bundle.exists()
        with zipfile.ZipFile(bundle) as z:
            names=set(z.namelist())
            assert 'config-redacted.json' in names
            assert 'webagent.db' in names
