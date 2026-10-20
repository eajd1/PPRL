"""Run isolated actual-Nginx integration tests; requires Docker Compose v2.24.4+."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_certificates import generate

root = Path(__file__).resolve().parents[1]
os.chdir(root)
credentials = root / '.test-credentials'
if not credentials.exists():
    generate(credentials, ['broker-proxy', 'localhost', '127.0.0.1'], 'integration-test-password')
test_data = tempfile.TemporaryDirectory(prefix='broker-data-', dir=credentials)
env = {**os.environ, 'BROKER_TLS_DIR': './.test-credentials/broker',
       'BROKER_TEST_DATA_DIR': Path(test_data.name).as_posix()}
compose = ['docker', 'compose', '-p', 'pprl-security-test', '-f', 'compose.broker.yaml', '-f', 'compose.test.yaml']
try:
    subprocess.run(compose + ['build'], env=env, check=True)
    subprocess.run(compose + ['up', '-d', 'broker-api', 'broker-proxy'], env=env, check=True)
    result = subprocess.run(compose + ['run', '--rm', 'test-runner'], env=env)
    if result.returncode:
        subprocess.run(compose + ['logs', '--tail', '80'], env=env)
    sys.exit(result.returncode)
finally:
    # Removes only this test project's disposable data; never the application volumes.
    subprocess.run(compose + ['down', '--volumes'], env=env, check=False)
    test_data.cleanup()
