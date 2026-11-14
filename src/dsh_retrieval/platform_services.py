"""Complete business wiring; platform integrations implement data protocols only."""
from pathlib import Path

from .platform_search import PlatformCatalogSource, PlatformFirstSearch
from .text_catalog_config import TextCatalogConfigService
from .adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
from .confirmed_cleaning import ConfirmedCleaningService
from .cleaning_dispatch import CleaningDispatchCoordinator
from .vlm_registry import ConfirmedTaskVLMRegistry
from .delivery import ImageDeliveryService
from .search_dispatch import SearchDispatchCoordinator
from .refill_runtime import build_refill_runtime
from .refill_service import ConfirmedRefillService


def build_platform_services(app, config, hooks, *, start=True):
    ledger = Path(config['task_ledger'])
    catalog_path = Path(config['catalog_ledger'])
    catalog_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = PlatformCatalogSource(app.resources)
    configs = TextCatalogConfigService(catalog_path, app.runtime, source.inventory, source.authorize)
    first = PlatformFirstSearch(app.service, configs, resources=app.resources, ledger_path=ledger,
        cursor_secret=app.resources.cursor_secret, capabilities=lambda: hooks['capabilities'](app),
        encode_query=lambda *args: hooks['encode_query'](app, *args),
        encode_reference=(lambda *args: hooks['encode_reference'](app, *args)) if hooks.get('encode_reference') else None,
        start=start)
    app.closers.append(first)
    approvals = SQLiteMultiRuleApprovalRepository(ledger, projects=app.projects, authorization=app.resources)
    cleaning = ConfirmedCleaningService(approvals,
        registry_provider=ConfirmedTaskVLMRegistry(app.runtime), gateway=app.resources)
    cleaning_dispatch = CleaningDispatchCoordinator(approvals, cleaning, start=start)
    app.closers.append(cleaning_dispatch)
    refill = build_refill_runtime(ledger, app.service, first, start=start)
    app.closers.append(refill)
    refill_service = ConfirmedRefillService(app.repository, approvals, refill)
    refill.enable_auto(approvals, refill_service, start=start)
    refill.enable_auto_intake(app.repository, first, cleaning, cleaning_dispatch, refill_service, start=start)
    search_dispatch = SearchDispatchCoordinator(app.repository, first, start=start)
    app.closers.append(search_dispatch)
    return {'catalog_config': configs, 'first_search': first,
        'plan_set_repository': first.repository, 'search_dispatch': search_dispatch,
        'cleaning_reader': approvals, 'cleaning_service': cleaning, 'cleaning_dispatch': cleaning_dispatch,
        'delivery_service': ImageDeliveryService(approvals, app.resources), 'refill_service': refill_service}
