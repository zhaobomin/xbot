"""Integration regressions for crew deadlines and automatic memory archival."""
import asyncio
from unittest.mock import AsyncMock
import pytest
from claude_agent_sdk import ResultMessage, SystemMessage
from xbot.platform.config.schema import Config
from xbot.runtime.core.service import AgentService
from xbot.runtime.core.types import AgentConfig
from xbot.runtime.core.protocol import StructuredLLMResponse, ToolCall
from xbot.runtime.session.conversation_store import ConversationStore
from xbot.crew.models import CrewConfig, AgentRole, TaskDefinition, OutputConfig
from xbot.crew.orchestrator import CrewOrchestrator

class FakeSDK:
    delay = 0
    instances = []
    def __init__(self, options):
        self.options = options
        self.cancelled = False
        self.disconnected = False
        self.__class__.instances.append(self)
    async def connect(self, prompt=None): pass
    async def disconnect(self): self.disconnected = True
    async def get_server_info(self): return {'commands': []}
    async def query(self, prompt): pass
    async def receive_messages(self):
        try:
            await asyncio.sleep(self.delay)
            yield ResultMessage(subtype='success', duration_ms=0, duration_api_ms=0,
                                is_error=False, num_turns=1, session_id='review-sdk', result='done')
            yield SystemMessage(subtype='session_state_changed', data={'state': 'idle'})
        except asyncio.CancelledError:
            self.cancelled = True
            raise

@pytest.mark.asyncio
async def test_07_explicit_timeout_is_enforced_through_orchestrator(tmp_path, monkeypatch):
    monkeypatch.setattr('claude_agent_sdk.ClaudeSDKClient', FakeSDK)
    monkeypatch.setattr(FakeSDK, 'delay', 1.15)
    monkeypatch.setattr(FakeSDK, 'instances', [])
    task = TaskDefinition(name='slow', description='answer', agent='worker', timeout=1)
    cfg = CrewConfig(name='review', workspace=str(tmp_path),
        agents={'worker': AgentRole(name='worker', description='test', goal='answer')},
        tasks=[task], output=OutputConfig(enabled=False))
    result = await CrewOrchestrator(crew_config=cfg, xbot_config=Config(),
                                    permission_handler=None).run()
    assert len(result.task_results) == 1
    actual = result.task_results[0]
    assert actual.status == 'failed', f'configured timeout=1s but result={actual.status}, duration={result.total_time:.3f}s'
    assert FakeSDK.instances[0].cancelled, 'deadline must cancel receive loop'
    assert FakeSDK.instances[0].disconnected, 'deadline must shut down SDK execution'

@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['sync', 'async'])
async def test_08_automatic_memory_runs_from_real_process_direct(tmp_path, monkeypatch, mode):
    monkeypatch.setattr('claude_agent_sdk.ClaudeSDKClient', FakeSDK)
    monkeypatch.setattr(FakeSDK, 'delay', 0)
    cfg = Config()
    cfg.agents.claude_sdk.memory_consolidation_mode = mode
    cfg.agents.defaults.context_window_tokens = 10000
    store = ConversationStore(tmp_path)
    service = AgentService()
    await service.initialize(AgentConfig(model='claude-sonnet-4-6', system_prompt='test'),
        {'workspace': str(tmp_path), 'config': cfg, 'conversation_store': store})
    session = store.get_or_create('cli:review')
    for i in range(30):
        session.add_message('user' if i % 2 == 0 else 'assistant', 'word ' * 2000)
    store.save(session)
    # This replaces only the secondary LLM network call, not consolidation logic.
    network = AsyncMock(return_value=StructuredLLMResponse(content='', tool_calls=[
        ToolCall(name='save_memory', arguments={'history_entry':'archived', 'memory_update':'memory'})]))
    monkeypatch.setattr(service, 'call_for_consolidation', network)
    try:
        assert await service.process_direct('one more message', 'cli:review') == 'done'
        pending = list(service._async_consolidation_tasks)
        if pending:
            await asyncio.gather(*pending)
        assert network.await_count > 0, f'mode={mode}: real turn never called consolidation LLM'
        assert session.last_consolidated > 0
        reloaded = ConversationStore(tmp_path).get('cli:review')
        assert reloaded.last_consolidated == session.last_consolidated, 'archive offset must survive restart'
    finally:
        await service.shutdown()

@pytest.mark.asyncio
async def test_08_archive_offset_survives_restart_when_trigger_is_restored(tmp_path):
    """Secondary fix contract: real consolidation + persistence; fake only LLM network."""
    from xbot.memory.store import MemoryConsolidator
    store = ConversationStore(tmp_path)
    session = store.get_or_create('cli:offset')
    for i in range(20):
        session.add_message('user' if i % 2 == 0 else 'assistant', 'word ' * 2000)
    store.save(session)
    backend = AgentService()
    backend.call_for_consolidation = AsyncMock(return_value=StructuredLLMResponse(tool_calls=[
        ToolCall(name='save_memory', arguments={'history_entry':'archived', 'memory_update':'memory'})]))
    consolidator = MemoryConsolidator(tmp_path, backend, store, 10000,
                                      lambda **kw: kw['history'], lambda: [])
    await consolidator.maybe_consolidate_by_tokens(session)
    assert backend.call_for_consolidation.await_count > 0
    assert session.last_consolidated > 0
    reloaded = ConversationStore(tmp_path).get('cli:offset')
    assert reloaded.last_consolidated == session.last_consolidated, (
        f'live offset={session.last_consolidated}; disk offset={reloaded.last_consolidated}')

@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['sync', 'async'])
async def test_worker_archives_only_after_success_and_idle_without_blocking(tmp_path, mode):
    from types import SimpleNamespace
    from xbot.runtime.core.service import SessionWorker
    from xbot.runtime.core.types import AgentResponse
    service = AgentService()
    cfg = Config()
    cfg.agents.claude_sdk.memory_consolidation_mode = mode
    store = ConversationStore(tmp_path)
    store.get_or_create('worker:test')
    service._shared_resources = {'config': cfg, 'conversation_store': store}
    entered, release = asyncio.Event(), asyncio.Event()
    async def consolidate(session):
        entered.set()
        await release.wait()
    service._memory_consolidator = SimpleNamespace(maybe_consolidate_by_tokens=AsyncMock(side_effect=consolidate))
    service._sync_sdk_session_mapping = lambda *args: None
    service._observe_sdk_message = lambda *args: None
    service._publish_worker_response = AsyncMock()
    service._dispatch_state_event = lambda *args, **kw: None
    service._dispatch_terminal_state = lambda *args, **kw: None
    worker = SessionWorker('worker:test', None, asyncio.Queue(), None, 'test', 'test')
    service._convert_event = lambda msg: msg if isinstance(msg, AgentResponse) else None
    idle = SystemMessage(subtype='session_state_changed', data={'state': 'idle'})
    await service._handle_worker_sdk_message(worker, AgentResponse(content='', event_type='result', finish_reason='error'), None)
    await service._handle_worker_sdk_message(worker, idle, None)
    assert not service._async_consolidation_tasks
    await service._handle_worker_sdk_message(worker, AgentResponse(event_type='result', content='ok'), None)
    assert not service._async_consolidation_tasks
    await asyncio.wait_for(service._handle_worker_sdk_message(worker, idle, None), .1)
    await entered.wait()
    session = store.get('worker:test')
    await service._trigger_memory_consolidation('worker:test', session, background=True)
    assert len(service._async_consolidation_tasks) == 1
    gate = asyncio.create_task(service._wait_for_memory_consolidation('worker:test'))
    await asyncio.sleep(0)
    assert gate.done() is (mode == 'async')
    release.set()
    await gate
    await asyncio.gather(*service._async_consolidation_tasks)
    assert service._memory_consolidator.maybe_consolidate_by_tokens.await_count == 1

@pytest.mark.asyncio
async def test_reset_cancels_archival_before_session_can_be_deleted(tmp_path):
    from types import SimpleNamespace
    service = AgentService()
    cfg = Config()
    cfg.agents.claude_sdk.memory_consolidation_mode = 'async'
    store = ConversationStore(tmp_path)
    session = store.get_or_create('cli:reset')
    session.add_message('user', 'old')
    store.save(session)
    service._shared_resources = {'config': cfg, 'conversation_store': store}
    entered = asyncio.Event()
    async def consolidate(session):
        entered.set()
        await asyncio.Event().wait()
        session.last_consolidated = 1
        session.mark_metadata_dirty()
        store.save(session)
    service._memory_consolidator = SimpleNamespace(maybe_consolidate_by_tokens=consolidate)
    await service._trigger_memory_consolidation(session.key, session)
    await entered.wait()
    await service.reset_session(session.key)
    store.delete(session.key)
    await asyncio.sleep(0)
    assert not service._async_consolidation_tasks
    assert ConversationStore(tmp_path).get(session.key) is None

@pytest.mark.asyncio
async def test_external_crew_cancellation_closes_stream_and_only_stops_exact_session():
    from types import SimpleNamespace
    from xbot.crew.process import BaseProcess
    started, closed = asyncio.Event(), asyncio.Event()
    async def stream(*args):
        try:
            started.set()
            await asyncio.Event().wait()
            yield None
        finally:
            closed.set()
    pool = SimpleNamespace(run_task_streaming=stream, stop_task=AsyncMock())
    process = SimpleNamespace(pool=pool, _pool_supports_native_streaming=lambda: True)
    task = TaskDefinition(name='slow', description='answer', agent='worker', timeout=None)
    running = asyncio.create_task(BaseProcess._execute_task(process, task, 'prompt', 'crew:exact'))
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert closed.is_set()
    pool.stop_task.assert_awaited_once_with('worker', 'crew:exact')

@pytest.mark.asyncio
async def test_consolidation_failure_does_not_fail_completed_reply(tmp_path, monkeypatch):
    monkeypatch.setattr('claude_agent_sdk.ClaudeSDKClient', FakeSDK)
    monkeypatch.setattr(FakeSDK, 'delay', 0)
    cfg = Config()
    cfg.agents.claude_sdk.memory_consolidation_mode = 'sync'
    service = AgentService()
    await service.initialize(AgentConfig(model='claude-sonnet-4-6', system_prompt='test'),
        {'workspace': str(tmp_path), 'config': cfg, 'conversation_store': ConversationStore(tmp_path)})
    service._memory_consolidator.maybe_consolidate_by_tokens = AsyncMock(side_effect=RuntimeError('offline'))
    try:
        assert await service.process_direct('hello') == 'done'
    finally:
        await service.shutdown()

@pytest.mark.asyncio
async def test_shutdown_drains_memory_job_and_prevents_rescheduling():
    from types import SimpleNamespace
    service = AgentService()
    service._initialized = True
    cfg = Config()
    cfg.agents.claude_sdk.memory_consolidation_mode = 'async'
    service._shared_resources = {'config': cfg}
    entered = asyncio.Event()
    async def consolidate(session):
        entered.set()
        await asyncio.Event().wait()
    service._memory_consolidator = SimpleNamespace(maybe_consolidate_by_tokens=consolidate)
    await service._trigger_memory_consolidation('test', object())
    await entered.wait()
    await service.shutdown()
    await service._trigger_memory_consolidation('test', object())
    assert not service._async_consolidation_tasks
    assert not service._consolidation_by_session

@pytest.mark.asyncio
async def test_force_archive_offset_survives_restart(tmp_path):
    from xbot.memory.store import MemoryConsolidator
    store = ConversationStore(tmp_path)
    session = store.get_or_create('cli:force')
    session.add_message('user', 'remember this')
    store.save(session)
    backend = AgentService()
    backend.call_for_consolidation = AsyncMock(return_value=StructuredLLMResponse(tool_calls=[
        ToolCall(name='save_memory', arguments={'history_entry':'archived', 'memory_update':'memory'})]))
    consolidator = MemoryConsolidator(tmp_path, backend, store, 10000,
                                      lambda **kw: kw['history'], lambda: [])
    await consolidator.force_consolidate(session, reserve_last_n=0)
    assert ConversationStore(tmp_path).get(session.key).last_consolidated == 1

@pytest.mark.asyncio
async def test_cancelling_direct_sync_wait_releases_progress_callback(monkeypatch):
    service = AgentService()
    waiting = asyncio.Event()
    async def wait(_key):
        waiting.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(service, '_wait_for_memory_consolidation', wait)
    task = asyncio.create_task(service.process_direct('hello', 'cli:test', on_progress=AsyncMock()))
    await waiting.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert 'cli:test' not in service._direct_progress_callbacks

@pytest.mark.asyncio
async def test_worker_admission_does_not_wait_for_archival(monkeypatch):
    from types import SimpleNamespace
    from xbot.platform.bus.events import InboundMessage
    service = AgentService()
    worker = SimpleNamespace(session_key='im:telegram:1', input_queue=asyncio.Queue(), channel='',chat_id='',last_idle_at=None)
    monkeypatch.setattr(service, '_get_or_start_session_worker', AsyncMock(return_value=worker))
    blocked = AsyncMock(side_effect=AssertionError('Global ingress must not await archival'))
    monkeypatch.setattr(service, '_wait_for_memory_consolidation', blocked)
    msg = InboundMessage(channel='telegram',sender_id='1',chat_id='1',content='hello')
    await service._enqueue_worker_message(msg, AsyncMock())
    blocked.assert_not_awaited()
    assert not worker.input_queue.empty()
