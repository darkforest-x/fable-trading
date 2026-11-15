from pathlib import Path
import os
import sys


TOOLS = Path(__file__).resolve().parents[2] / 'yoyo/discipline_app/native/tools'
sys.path.insert(0, str(TOOLS))
import renew_ios


NOW = 1_800_000_000
VERSION = ('2.9.7', '34')
CONFIG = {'version': VERSION[0], 'build': VERSION[1], 'expires': NOW + 2 * renew_ios.DAY}
CANDIDATE = {
    'version': VERSION[0],
    'build': VERSION[1],
    'expires': NOW + 5 * renew_ios.DAY,
    'path': '/tmp/SpikeDiscipline.app',
}


class FakeServices:
    def __init__(self, installed=None, verify=None, candidate=None):
        self.installed_values = list(installed if installed is not None else [VERSION, VERSION])
        self.verify_values = list(verify) if verify is not None else None
        self.candidate = candidate if candidate is not None else CANDIDATE
        self.prepare_calls = 0
        self.install_calls = []
        self.saved = []

    def prepare(self, config, now):
        self.prepare_calls += 1
        return self.candidate

    def installed(self, config):
        values = self.verify_values if self.verify_values is not None else self.installed_values
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def install(self, config, candidate):
        self.install_calls.append(candidate)

    def save(self, config):
        self.saved.append(config)


def test_renewal_waits_until_expiry_is_within_48_hours():
    services = FakeServices()
    config = dict(CONFIG, expires=NOW + 2 * renew_ios.DAY + 1)

    result = renew_ios.run_cycle(services, config, NOW)

    assert result == {'status': 'scheduled'}
    assert services.prepare_calls == 0
    assert services.install_calls == []


def test_offline_enrollment_only_accepts_the_exact_signed_version_and_build():
    evidence = renew_ios.offline_install_evidence(CANDIDATE, '2.9.7', '34', NOW)

    assert evidence['source'] == 'prior_device_inspection'
    assert evidence['version'] == '2.9.7'
    assert evidence['build'] == '34'
    assert evidence['profile_check'] == 'pending_until_device_available'


def test_offline_enrollment_rejects_an_unmatched_prior_build():
    try:
        renew_ios.offline_install_evidence(CANDIDATE, '2.9.7', '35', NOW)
    except ValueError as error:
        assert '完全一致' in str(error)
    else:
        raise AssertionError('mismatched prior build should be rejected')


def test_runner_does_not_forward_unrelated_api_credentials_to_subprocesses():
    prior = os.environ.get('XAI_API_KEY')
    os.environ['XAI_API_KEY'] = 'test-only-sentinel'
    try:
        renew_ios.sanitize_environment()
        assert 'XAI_API_KEY' not in os.environ
    finally:
        if prior is None:
            os.environ.pop('XAI_API_KEY', None)
        else:
            os.environ['XAI_API_KEY'] = prior


def test_offline_device_is_retried_and_same_release_renews_when_available():
    services = FakeServices(installed=[renew_ios.DeviceUnavailable('offline'), VERSION, VERSION])

    first = renew_ios.run_cycle(services, dict(CONFIG), NOW)
    second = renew_ios.run_cycle(services, dict(CONFIG), NOW + 3600)

    assert first == {'status': 'waiting_device'}
    assert second == {'status': 'renewed'}
    assert services.prepare_calls == 2
    assert len(services.install_calls) == 1
    assert services.saved[0]['expires'] == CANDIDATE['expires']


def test_candidate_with_different_version_or_build_is_never_installed():
    candidate = dict(CANDIDATE, build='35')
    services = FakeServices(candidate=candidate)

    result = renew_ios.run_cycle(services, dict(CONFIG), NOW)

    assert result == {'status': 'waiting_profile'}
    assert services.install_calls == []
    assert services.saved == []


def test_candidate_must_extend_the_existing_profile_expiry():
    candidate = dict(CANDIDATE, expires=CONFIG['expires'])
    services = FakeServices(candidate=candidate)

    result = renew_ios.run_cycle(services, dict(CONFIG), NOW)

    assert result == {'status': 'waiting_profile'}
    assert services.install_calls == []
    assert services.saved == []


def test_changed_installed_version_is_never_replaced():
    services = FakeServices(installed=[('2.9.8', '34')])

    result = renew_ios.run_cycle(services, dict(CONFIG), NOW)

    assert result == {'status': 'version_changed'}
    assert services.install_calls == []
    assert services.saved == []


def test_failed_post_install_version_check_does_not_advance_expiry():
    services = FakeServices(verify=[VERSION, ('2.9.8', '34')])

    result = renew_ios.run_cycle(services, dict(CONFIG), NOW)

    assert result == {'status': 'verification_failed'}
    assert len(services.install_calls) == 1
    assert services.saved == []
