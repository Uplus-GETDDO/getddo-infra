"""Terminal SQL grants and operator installation without AWS or database writes."""
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from staging_hooks import host
from staging_locust_install import install_operator
from test_staging_lifecycle import manifest


class TeamAccessTests(unittest.TestCase):
    def deployment(self):
        value = manifest()
        value['deployment'] = {
            'db_identifier': 'getddo-staging-mysql',
            'db_endpoint': 'getddo-staging-mysql.fixture.ap-northeast-2.rds.amazonaws.com',
            'locust_instance': {'private_ip': '10.20.0.42'},
        }
        return value

    def test_seed_formats_wildcard_host_and_grants_only_data_operations(self):
        value = self.deployment()
        actor = {'user_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                 'role': 'USER', 'membership': 'excellent'}
        # Use PyMySQL's real interpolation and escaping without opening a socket.
        formatter_connection = pymysql.connect(defer_connect=True)
        formatter_connection.server_status = 0
        formatter = formatter_connection.cursor()
        statements = []
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        cursor.fetchone.return_value = None
        cursor.execute.side_effect = lambda query, args=None: statements.append(formatter.mogrify(query, args))
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.cursor.return_value = cursor
        app = {'SPRING_DATASOURCE_URL': 'jdbc:mysql://' + value['deployment']['db_endpoint'] + '/getddo',
               'SPRING_DATASOURCE_USERNAME': 'app', 'SPRING_DATASOURCE_PASSWORD': 'app-test-password'}
        password = "test%password'quoted"
        body = io.BytesIO(json.dumps({'success': True, 'code': 'SUCCESS', 'data': {'items': []}}).encode())
        with patch.object(host, 'parameters', side_effect=[app, {'mysql-password': password}]), \
                patch('pymysql.connect', return_value=connection), \
                patch.object(host.ssl, 'create_default_context'), \
                patch.object(host.urllib.request, 'urlopen', return_value=body), \
                patch('builtins.print') as output:
            host.seed(value, actor)
        user_statements = [query for query in statements if query.startswith(('CREATE USER', 'ALTER USER'))]
        self.assertEqual(len(user_statements), 2)
        for query in user_statements:
            self.assertIn("'stage_tester'@'%'", query)
            self.assertIn('REQUIRE SSL', query)
            self.assertIn(formatter_connection.escape(password), query)
        grants = [query for query in statements if query.startswith('GRANT ')]
        self.assertEqual(grants, ["GRANT SELECT, INSERT, UPDATE, DELETE ON getddo.* TO 'stage_tester'@'%'"])
        self.assertFalse(any(word in grants[0] for word in ('ALL PRIVILEGES', 'GRANT OPTION', 'CREATE', 'DROP', 'ALTER')))
        output.assert_called_once_with('STAGE_SEED_OK')

    def test_operator_installs_private_configuration_and_editable_examples(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source'
            examples = source/'loadtest/examples'
            examples.mkdir(parents=True)
            (examples/'events.py').write_text('# event scenario\n')
            (examples/'seed.sql').write_text('SELECT 1;\n')
            (examples/'ignored.txt').write_text('not a scenario')
            (examples/'linked.py').symlink_to(examples/'events.py')
            config_dir = root/'etc/getddo-loadtest'
            config_dir.mkdir(mode=0o700, parents=True)
            previous_umask = os.umask(0o077)
            try:
                install_operator(self.deployment(), source, root)
            finally:
                os.umask(previous_umask)
            base = root/'opt/getddo-loadtest'
            self.assertEqual(stat.S_IMODE(base.stat().st_mode), 0o755)
            config_path = config_dir/'operator.json'
            config = json.loads(config_path.read_text())
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertEqual(config_path.stat().st_uid, os.getuid())
            self.assertEqual(config['db_user'], 'stage_tester')
            self.assertEqual(config['db_password_parameter'], '/getddo/staging/test-access/mysql-password')
            self.assertNotIn('db_password', config)
            self.assertEqual(config['expected_workers'], 2)
            self.assertTrue((base/'active').is_symlink())
            self.assertEqual((base/'active').resolve(), base/'releases/default')
            for path in (base/'scenarios', base/'scenarios/gyeongju'):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
            self.assertEqual({p.name for p in (base/'scenarios/gyeongju').iterdir()}, {'events.py', 'seed.sql'})
            wrapper = root/'usr/local/bin/loadtest'
            self.assertEqual(stat.S_IMODE(wrapper.stat().st_mode), 0o755)
            self.assertIn('exec sudo /opt/getddo-staging-venv/bin/python ', wrapper.read_text())
            self.assertIn('scripts/loadtest_admin.py', wrapper.read_text())
            self.assertTrue(wrapper.read_text().endswith(' "$@"\n'))
            # Reinstallation preserves the operator's edits and selected release.
            personal = base/'scenarios/gyeongju/events.py'
            personal.write_text('# personal modification\n')
            install_operator(self.deployment(), source, root)
            self.assertEqual(personal.read_text(), '# personal modification\n')

    def test_operator_rejects_redirected_scenario_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root/'opt/getddo-loadtest'
            base.mkdir(parents=True)
            (base/'scenarios').symlink_to(root)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                install_operator(self.deployment(), root, root)


if __name__ == '__main__':
    unittest.main()
