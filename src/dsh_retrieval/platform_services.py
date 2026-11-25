"""Complete business wiring; platform integrations implement data protocols only."""
from pathlib import Path

from .platform_search import PlatformCatalogSource, PlatformFirstSearch
from .text_catalog_config import TextCatalogConfigService, CatalogConfigScope
from .adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
from .confirmed_cleaning import ConfirmedCleaningService
from .cleaning_dispatch import CleaningDispatchCoordinator
from .vlm_registry import ConfirmedTaskVLMRegistry
from .video_runtime import VideoExecutionPolicy, MediaRegistryProvider
from .video_delivery import MediaDeliveryService
from .search_dispatch import SearchDispatchCoordinator
from .refill_runtime import build_refill_runtime
from .refill_service import ConfirmedRefillService
from .video_initial import InitialVideoIntake


def build_platform_services(app, config, hooks, *, start=True):
    from .search_scheduling import SearchLimits
    search_limits = SearchLimits(**config.get('search_limits', {}))
    ledger = Path(config['task_ledger'])
    catalog_path = Path(config['catalog_ledger'])
    catalog_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = PlatformCatalogSource(app.resources)
    configs = TextCatalogConfigService(catalog_path, app.runtime, source.inventory, source.authorize)
    first = PlatformFirstSearch(app.service, configs, resources=app.resources, ledger_path=ledger,
        cursor_secret=app.resources.cursor_secret, capabilities=lambda: hooks['capabilities'](app),
        encode_query=lambda *args: hooks['encode_query'](app, *args),
        encode_reference=(lambda *args: hooks['encode_reference'](app, *args)) if hooks.get('encode_reference') else None,
        start=start, search_limits=search_limits)
    app.closers.append(first)
    approvals = SQLiteMultiRuleApprovalRepository(ledger, projects=app.projects, authorization=app.resources)
    video_registry = ((lambda actor, task: hooks['video_registry'](app, actor, task))
                      if callable(hooks.get('video_registry')) else None)
    cleaning = ConfirmedCleaningService(approvals,
        registry_provider=MediaRegistryProvider(ConfirmedTaskVLMRegistry(app.runtime), video_registry),
        gateway=app.resources)
    app.service.video_execution = VideoExecutionPolicy(app.projects,
        libraries=lambda actor, project: source.inventory(CatalogConfigScope(actor.tenant_id, actor.user_id, project)),
        capabilities=lambda: hooks['capabilities'](app),
        whole_video_bindings=(lambda: hooks['whole_video_bindings'](app)) if callable(hooks.get('whole_video_bindings')) else None,
        preflight=(lambda actor, project, spec: hooks['video_preflight'](app, actor, project, spec)) if callable(hooks.get('video_preflight')) else None,
        registry=video_registry, inspector=hooks.get('inspect_video_file') or hooks.get('inspect_video'))
    cleaning_dispatch = CleaningDispatchCoordinator(approvals, cleaning, start=start)
    app.closers.append(cleaning_dispatch)
    video_initial = InitialVideoIntake(app.service, first, cleaning, cleaning_dispatch, start=start)
    app.closers.append(video_initial)
    refill = build_refill_runtime(ledger, app.service, first, start=start)
    app.closers.append(refill)
    refill_service = ConfirmedRefillService(app.repository, approvals, refill)
    refill.enable_auto(approvals, refill_service, start=start)
    refill.enable_auto_intake(app.repository, first, cleaning, cleaning_dispatch, refill_service, start=start)
    search_dispatch = SearchDispatchCoordinator(app.repository, first, start=start)
    app.closers.append(search_dispatch)
    return {'catalog_config': configs, 'first_search': first,
        'plan_set_repository': first.repository, 'search_dispatch': search_dispatch,
        'video_initial_intake': video_initial, 'cleaning_reader': approvals, 'cleaning_service': cleaning, 'cleaning_dispatch': cleaning_dispatch,
        'delivery_service': MediaDeliveryService(approvals, app.resources,
            video_inspector=(lambda content: hooks['inspect_video'](app, content)) if hooks.get('inspect_video') else None,
            video_file_inspector=(lambda path: hooks['inspect_video_file'](app, path)) if hooks.get('inspect_video_file') else None,
            policy=app.resources.video_policy), 'refill_service': refill_service}
