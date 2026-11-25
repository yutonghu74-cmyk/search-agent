"""Platform-only assembly. Run behind the platform HTTPS reverse proxy.

The administrator supplies a reviewed protocol integration factory. This avoids
inventing PAT response fields, dataset/version mappings, or private endpoints.
"""
import argparse
import importlib
import json
import hmac
from pathlib import Path
from http.server import ThreadingHTTPServer

from .adapters.platform_access import PlatformClient, PlatformIdentity, PlatformProjectAccess
from .adapters.task_storage.sqlite import SQLiteTaskRepository
from .api import create_handler
from .dialogue import DialogueCoordinator, DialogueStore
from .expansion_jobs import ExpansionJobs
from .model_runtime import load_model_runtime, ModelRuntime
from .platform_browser import PlatformBrowser
from .platform_credentials import DelegatedCredentials
from .platform_resources import PlatformResources
from .query_images import QueryImageService
from .service import TaskService


REQUIRED_HOOKS = ('extract_user_id', 'exchange', 'list_projects', 'project_datasets',
                  'user_datasets', 'libraries', 'resolve_hits', 'search', 'open_media',
                  'media_version', 'capabilities', 'encode_query')


class PlatformApplication:
    def __init__(self, config, hooks, *, start_workers=True):
        required = {'api_base_url', 'tenant_id', 'task_ledger', 'dialogue_ledger',
                    'credential_store', 'credential_key', 'catalog_ledger', 'allowed_actions'}
        if not required <= set(config) or set(config) - required - {'model_config', 'frame_origins', 'dsh_python', 'dsh_home', 'protocol_config', 'video_resources', 'search_limits'}:
            raise ValueError('invalid platform configuration keys')
        if any(not callable(hooks.get(key)) for key in REQUIRED_HOOKS):
            raise ValueError('verified platform protocol integration hooks are required')
        if 'encode_reference' in hooks and not callable(hooks['encode_reference']):
            raise ValueError('optional reference encoder must be callable')
        if 'inspect_video' in hooks and not callable(hooks['inspect_video']):
            raise ValueError('optional whole-video inspector must be callable')
        for name in ('video_preflight', 'video_registry', 'whole_video_bindings', 'inspect_video_file'):
            if name in hooks and not callable(hooks[name]):
                raise ValueError('optional video integration hooks must be callable')
        paths = [Path(config[key]).expanduser().resolve() for key in
                 ('task_ledger', 'dialogue_ledger', 'credential_store', 'credential_key', 'catalog_ledger')]
        if len(set(paths)) != len(paths):
            raise ValueError('platform state and key files must be independent')
        if 'read' not in config['allowed_actions']:
            raise ValueError('platform action whitelist must include read')
        if bool(config.get('dsh_python')) != bool(config.get('dsh_home')):
            raise ValueError('DSH requires both explicit python and isolated home')
        self.closers = []
        try:
            self.client = PlatformClient(config['api_base_url'])
            self.identity = PlatformIdentity(self.client, config['tenant_id'], extract_user_id=hooks['extract_user_id'])
            self.credentials = DelegatedCredentials(config['credential_store'], config['credential_key'],
                identity=self.identity, exchange=lambda key: hooks['exchange'](self.client, key))
            self.closers.append(self.credentials)
            self.projects = PlatformProjectAccess(self.client, config['tenant_id'],
                credential_for_actor=self.credentials.credential_for_actor,
                allowed_actions=config['allowed_actions'], extract_user_id=hooks['extract_user_id'],
                credential_invalidated=self.credentials.revoke)
            self.repository = SQLiteTaskRepository(Path(config['task_ledger']))
            if hasattr(self.repository, 'close'):
                self.closers.append(self.repository)
            self.service = TaskService(self.repository, self.projects)
            self.resources = PlatformResources(tasks=self.repository, projects=self.projects,
                cursor_secret=hmac.digest(self.credentials.box.mac, b'platform-cursors/v1', 'sha256'),
                **{key: (lambda *args, _key=key: hooks[_key](self, *args)) for key in
                   ('project_datasets', 'user_datasets', 'libraries', 'resolve_hits', 'search', 'open_media', 'media_version')})
            from .video_media import VideoResourcePolicy
            self.resources.video_policy = VideoResourcePolicy(**config.get('video_resources', {}))
            self.resources.video_inspector = ((lambda content: hooks['inspect_video'](self, content)) if callable(hooks.get('inspect_video')) else None)
            self.resources.video_file_inspector = ((lambda path: hooks['inspect_video_file'](self, path)) if callable(hooks.get('inspect_video_file')) else None)
            self.resources.whole_video_bindings = ((lambda: hooks['whole_video_bindings'](self))
                                                   if callable(hooks.get('whole_video_bindings')) else None)
            self.runtime = load_model_runtime(config['model_config']) if config.get('model_config') else ModelRuntime()
            if config.get('dsh_python'):
                from .adapters.dsh.runtime import DshModelRuntime
                self.runtime = DshModelRuntime(self.runtime.profiles.values(),
                    python=config['dsh_python'], home=config['dsh_home'])
                self.runtime.check_installation()
                self.runtime.recover()
            self.store = DialogueStore(Path(config['dialogue_ledger']))
            self.closers.append(self.store)
            self.dialogue = DialogueCoordinator(self.store, self.runtime.conversation_runtime(), self.projects)
            self.expansion = ExpansionJobs(Path(config['dialogue_ledger']), self.runtime,
                                           self.dialogue, self.projects, self.service)
            self.closers.append(self.expansion)
            from .platform_services import build_platform_services
            components = build_platform_services(self, config, hooks, start=start_workers)
            browser = PlatformBrowser(self.credentials, self.projects,
                list_projects=lambda actor, token: hooks['list_projects'](self.client, actor, token),
                frame_origins=config.get('frame_origins', ()), reference_search=bool(hooks.get('encode_reference')))
            images = QueryImageService(self.projects, Path(config['task_ledger']))
            self.service.query_images = images
            first_limits = components['first_search'].search_limits
            self.handler = create_handler(self.service, model_runtime=self.runtime,
                dialogue=self.dialogue, expansion_jobs=self.expansion, query_images=images,
                identity_provider=self.identity, identity_mode='platform', platform_browser=browser,
                build_info={'deployment_mode': 'platform', 'integration_status': 'REQUIRES_INTERNAL_ACCEPTANCE',
                            'search_execution_limits': {name: getattr(first_limits, name) for name in ('workers', 'max_pending', 'max_calls', 'max_hit_slots', 'max_pending_per_user')}},
                **components)
        except Exception:
            self.close()
            raise

    def recover(self):
        self.handler.task_commands.recover_interrupted()
        self.expansion.recover()

    def close(self):
        handler = getattr(self, 'handler', None)
        if handler is not None:
            handler.task_commands.close()
            self.handler = None
        failures = []
        expansion = getattr(self, 'expansion', None)
        if expansion in self.closers:
            try:
                expansion.close()
            except Exception:
                failures.append('ExpansionJobs')
            self.closers.remove(expansion)
        for component in reversed(self.closers):
            try:
                component.close()
            except Exception:
                failures.append(type(component).__name__)
        self.closers.clear()
        if failures:
            raise RuntimeError('platform shutdown failed for: ' + ', '.join(failures))


def main():
    parser = argparse.ArgumentParser(description='Platform deployment behind trusted HTTPS gateway')
    parser.add_argument('--config', required=True, help='private platform configuration JSON')
    parser.add_argument('--integration', default='dsh_retrieval.platform_protocol:create', help='reviewed installed module:factory; default uses declarative private wire mapping')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    module, separator, name = args.integration.partition(':')
    if not separator or not module or not name or '.' in name:
        parser.error('--integration must be module:factory')
    hooks = getattr(importlib.import_module(module), name)(config)
    app = PlatformApplication(config, hooks)
    server = None
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), app.handler)
        app.recover()
        print('Platform agent listening on loopback; HTTPS proxy and internal acceptance required', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        app.close()


if __name__ == '__main__':
    main()
