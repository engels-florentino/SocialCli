"""Deployment layers must not silently change the reviewed runtime."""
import configparser
from pathlib import Path
import yaml

ROOT = Path(__file__).parents[1] / 'deploy' / 'saas'


def test_pilot_overlay_inherits_reviewed_base_image():
    base = yaml.safe_load((ROOT / 'candidate.compose.yaml').read_text())
    overlay = yaml.safe_load((ROOT / 'pilot.compose.yaml').read_text())
    candidate = base['services']['candidate'] | overlay['services']['candidate']
    assert candidate['image'] == base['services']['candidate']['image']
    assert candidate['container_name'] == 'socialcli-saas-candidate'


def test_billing_timers_resolve_to_current_container():
    base = yaml.safe_load((ROOT / 'candidate.compose.yaml').read_text())
    for timer in ROOT.glob('*billing.timer'):
        config = configparser.ConfigParser(interpolation=None)
        config.read(timer)
        service = ROOT / config['Timer']['Unit']
        assert service.is_file(), timer.name
        config.read(service)
        expected = 'docker exec ' + base['services']['candidate']['container_name'] + ' '
        assert expected in config['Service']['ExecStart'], service.name
