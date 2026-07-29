<script setup lang="ts">
import { ref, computed } from 'vue'
import { ArrowDown, ArrowRight } from '@element-plus/icons-vue'
import type { DebugEventSummary, DebugEventDetailResponse } from '@/types/api'

interface EventGroup {
  correlation_id: string
  events: DebugEventSummary[]
  first_ts: number
}

const props = defineProps<{
  eventsByCorrelation: Map<string, DebugEventSummary[]>
  onLoadDetail?: (eventId: string) => Promise<DebugEventDetailResponse | undefined>
}>()

const emit = defineEmits<{
  clearAll: []
}>()

const expanded = ref<Set<string>>(new Set())
const details = ref<Map<string, DebugEventDetailResponse>>(new Map())
const detailLoading = ref<Set<string>>(new Set())

const groups = computed<EventGroup[]>(() => {
  const map = props.eventsByCorrelation
  const arr: EventGroup[] = []
  for (const [cid, evs] of map.entries()) {
    arr.push({ correlation_id: cid, events: evs, first_ts: evs[0]?.ts ?? 0 })
  }
  arr.sort((a, b) => b.first_ts - a.first_ts) // 新的在上
  return arr
})

async function toggle(eventId: string) {
  const s = new Set(expanded.value)
  if (s.has(eventId)) {
    s.delete(eventId)
  } else {
    s.add(eventId)
    if (!details.value.has(eventId) && props.onLoadDetail) {
      detailLoading.value.add(eventId)
      try {
        const d = await props.onLoadDetail(eventId)
        if (d) {
          details.value.set(eventId, d)
        }
      } finally {
        detailLoading.value.delete(eventId)
      }
    }
  }
  expanded.value = s
}

function clearAll() {
  emit('clearAll')
  expanded.value = new Set()
  details.value = new Map()
}

function directionLabel(d: string): string {
  const map: Record<string, string> = {
    inbound_request: '入站请求',
    inbound_response: '入站响应',
    upstream_request: '上游请求',
    upstream_response: '上游响应',
    upstream_request_final: '上游请求(完成)',
    upstream_response_final: '上游流式(汇总)',
    pipeline: '管线',
  }
  return map[d] ?? d
}

function directionTag(d: string): 'primary' | 'success' | 'warning' | 'danger' | 'info' {
  if (d.startsWith('inbound_request')) return 'info'
  if (d.startsWith('inbound_response')) return 'primary'
  if (d.startsWith('upstream_request')) return 'warning'
  if (d.startsWith('upstream_response')) return 'success'
  if (d === 'pipeline') return 'danger'
  return 'info'
}

/** 从 pipeline 事件的 url (pipeline:{kind}) 提取 event_kind */
function pipelineKind(ev: DebugEventSummary): string {
  if (ev.direction !== 'pipeline') return ''
  return ev.url.replace('pipeline:', '')
}

const PIPELINE_KIND_LABELS: Record<string, string> = {
  tool_policy: '工具策略',
  tool_transaction: '工具事务',
  tool_call_decision: '工具调用决策',
  trigger_reason: '触发原因',
  expressor_rewrite: 'Expressor 改写',
  cooldown_blocked: '冷却拦截',
}

function pipelineKindLabel(kind: string): string {
  return PIPELINE_KIND_LABELS[kind] ?? kind
}

/** 提取 pipeline 事件的摘要文字 (从 body_preview) */
function pipelineSummary(ev: DebugEventSummary): string {
  if (ev.direction !== 'pipeline' || !ev.body_preview) return ''
  const body = ev.body_preview as Record<string, unknown>
  const kind = pipelineKind(ev)
  switch (kind) {
    case 'tool_policy': {
      const stage = body.stage as string
      const removed = body.removed_tools as string[] | null
      if (stage === 'inbound' && removed?.length) return `入站过滤: 移除 ${removed.join(', ')}`
      if (stage === 'inbound') return '入站过滤: 无移除'
      return stage || ''
    }
    case 'tool_transaction':
      return `${body.tail_messages ?? 0} 条尾部消息`
    case 'tool_call_decision': {
      const removed = body.removed_calls as string[] | null
      const kept = body.kept_calls as string[] | null
      const parts: string[] = []
      if (kept?.length) parts.push(`保留 ${kept.length}`)
      if (removed?.length) parts.push(`移除 ${removed.length}`)
      return parts.join(' · ') || '无调用'
    }
    case 'trigger_reason':
      return (body.reason as string) || ''
    case 'expressor_rewrite':
      return `${body.original_length ?? 0} → ${body.rewritten_length ?? 0} 字符`
    case 'cooldown_blocked': {
      const violations = body.violations as string[] | null
      const scope = body.scope as string
      const prefix = scope === 'global' ? '全局' : ''
      return violations?.length ? `${prefix}拦截 ${violations.length} 个调用` : ''
    }
    default:
      return ''
  }
}

function formatBody(body: unknown): string {
  if (body === null || body === undefined) return ''
  if (typeof body === 'string') return body
  try {
    return JSON.stringify(body, null, 2)
  } catch {
    return String(body)
  }
}

function formatTs(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('zh-CN', { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0')
}
</script>

<template>
  <div class="debug-event-list">
    <div class="debug-header">
      <span class="section-label">事件流</span>
      <span class="hint">按 correlation_id 分组; 展开卡片查看完整 body</span>
      <el-button size="small" @click="clearAll" round>清空调试日志</el-button>
    </div>

    <div class="groups">
      <div v-for="g in groups" :key="g.correlation_id" class="group">
        <div class="group-head">
          <code>{{ g.correlation_id }}</code>
          <span class="group-meta">{{ g.events.length }} 事件</span>
        </div>
        <div class="events">
          <div v-for="ev in g.events" :key="ev.id" class="event">
            <div class="event-head" @click="toggle(ev.id)">
              <el-tag :type="directionTag(ev.direction)" size="small">
                {{ directionLabel(ev.direction) }}
              </el-tag>
              <!-- Pipeline 事件: 显示事件类型标签 + 摘要 -->
              <template v-if="ev.direction === 'pipeline'">
                <el-tag type="warning" size="small" effect="plain">
                  {{ pipelineKindLabel(pipelineKind(ev)) }}
                </el-tag>
                <span class="e-summary">{{ pipelineSummary(ev) }}</span>
              </template>
              <!-- HTTP 事件: 显示原有字段 -->
              <template v-else>
                <span v-if="ev.method" class="e-method">{{ ev.method }}</span>
                <span class="e-url">{{ ev.url }}</span>
                <span v-if="ev.port" class="e-port">:{{ ev.port }}</span>
                <span v-if="ev.agent" class="e-agent">[{{ ev.agent }}]</span>
                <span v-if="ev.status" class="e-status">{{ ev.status }}</span>
                <span v-if="ev.duration_ms !== null" class="e-dur">
                  {{ ev.duration_ms.toFixed(0) }}ms
                </span>
                <span v-if="ev.key_note" class="e-key">key: {{ ev.key_note }}</span>
              </template>
              <span class="e-time">{{ formatTs(ev.ts) }}</span>
              <el-icon class="e-caret">
                <ArrowDown v-if="expanded.has(ev.id)" />
                <ArrowRight v-else />
              </el-icon>
            </div>

            <div v-if="expanded.has(ev.id)" class="event-body">
              <div v-if="detailLoading.has(ev.id)" class="loading">加载中…</div>
              <!-- Pipeline 事件详情: 结构化展示 -->
              <template v-else-if="ev.direction === 'pipeline' && details.get(ev.id)">
                <div class="pipeline-detail">
                  <!-- Expressor 改写: 前后对比 -->
                  <template v-if="pipelineKind(ev) === 'expressor_rewrite'">
                    <div class="expressor-compare">
                      <div class="compare-col">
                        <div class="section-label">改写前 ({{ (details.get(ev.id)!.body_full as Record<string, unknown>)?.original_length ?? 0 }} 字符)</div>
                        <pre class="pre">{{ (details.get(ev.id)!.body_full as Record<string, unknown>)?.original_preview ?? '' }}</pre>
                      </div>
                      <div class="compare-col">
                        <div class="section-label">改写后 ({{ (details.get(ev.id)!.body_full as Record<string, unknown>)?.rewritten_length ?? 0 }} 字符)</div>
                        <pre class="pre">{{ (details.get(ev.id)!.body_full as Record<string, unknown>)?.rewritten_preview ?? '' }}</pre>
                      </div>
                    </div>
                    <div v-if="(details.get(ev.id)!.body_full as Record<string, unknown>)?.expression_style" class="pipeline-field">
                      <span class="field-label">表达风格:</span>
                      <code>{{ (details.get(ev.id)!.body_full as Record<string, unknown>)?.expression_style }}</code>
                    </div>
                  </template>
                  <!-- 工具策略: 列表展示 -->
                  <template v-else-if="pipelineKind(ev) === 'tool_policy'">
                    <div class="pipeline-field">
                      <span class="field-label">阶段:</span>
                      <code>{{ (details.get(ev.id)!.body_full as Record<string, unknown>)?.stage }}</code>
                    </div>
                    <div v-if="(details.get(ev.id)!.body_full as Record<string, unknown>)?.original_tools" class="pipeline-field">
                      <span class="field-label">原始工具:</span>
                      <code>{{ ((details.get(ev.id)!.body_full as Record<string, unknown>)?.original_tools as string[])?.join(', ') }}</code>
                    </div>
                    <div v-if="(details.get(ev.id)!.body_full as Record<string, unknown>)?.kept_tools" class="pipeline-field">
                      <span class="field-label">保留:</span>
                      <code class="kept">{{ ((details.get(ev.id)!.body_full as Record<string, unknown>)?.kept_tools as string[])?.join(', ') }}</code>
                    </div>
                    <div v-if="(details.get(ev.id)!.body_full as Record<string, unknown>)?.removed_tools" class="pipeline-field">
                      <span class="field-label">移除:</span>
                      <code class="removed">{{ ((details.get(ev.id)!.body_full as Record<string, unknown>)?.removed_tools as string[])?.join(', ') }}</code>
                    </div>
                  </template>
                  <!-- 其他 pipeline 事件: 通用 JSON 展示 -->
                  <template v-else>
                    <pre class="pre">{{ formatBody(details.get(ev.id)!.body_full) }}</pre>
                  </template>
                </div>
              </template>
              <!-- HTTP 事件详情: 原有展示 -->
              <template v-else-if="details.get(ev.id)">
                <div v-if="details.get(ev.id)!.stream_assembled" class="assembled">
                  <div class="section-label">汇总内容 ({{ details.get(ev.id)!.stream_chunks_count }} chunks)</div>
                  <pre class="pre">{{ details.get(ev.id)!.stream_assembled }}</pre>
                </div>
                <div v-if="details.get(ev.id)!.summary.headers" class="hdrs">
                  <div class="section-label">Headers</div>
                  <pre class="pre">{{ formatBody(details.get(ev.id)!.summary.headers) }}</pre>
                </div>
                <div class="body">
                  <div class="section-label">
                    Body ({{ details.get(ev.id)!.summary.body_full_size }} bytes<span
                      v-if="details.get(ev.id)!.summary.is_truncated"
                    >
                      · 已截断</span
                    >)
                  </div>
                  <pre class="pre">{{ formatBody(details.get(ev.id)!.body_full) }}</pre>
                </div>
              </template>
              <!-- 无详情时的预览 -->
              <div v-else class="preview">
                <pre class="pre">{{ formatBody(ev.body_preview) }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div v-if="!groups.length" class="empty">还没有捕获到事件。发一条消息试试。</div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.debug-event-list {
  display: flex;
  flex-direction: column;
  gap: $space-2;
  height: 100%;
  overflow: hidden;
}

.debug-header {
  display: flex;
  align-items: center;
  gap: $space-3;
  .hint {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    flex: 1;
  }
}

.section-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

.groups {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.group {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-sm;
  background: var(--el-fill-color-lighter);
}

.group-head {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: $space-2 $space-3;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 12px;
}

.group-meta { color: var(--el-text-color-secondary); }

.events { display: flex; flex-direction: column; }

.event { border-bottom: 1px solid var(--el-border-color-lighter); }
.event:last-child { border-bottom: 0; }

.event-head {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: 6px $space-3;
  cursor: pointer;
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 12px;

  &:hover { background: var(--el-fill-color); }

  .e-time { color: var(--el-text-color-secondary); }
  .e-summary {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--el-text-color-primary);
  }
  .e-method { font-weight: 600; }
  .e-url {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--el-text-color-primary);
  }
  .e-agent { color: var(--el-color-primary); }
  .e-status { color: var(--el-color-success); }
  .e-dur { color: var(--el-text-color-secondary); }
  .e-key { color: var(--el-text-color-secondary); font-size: 11px; }
  .e-caret { margin-left: auto; }
}

.event-body {
  padding: $space-2 $space-3;
  background: var(--el-bg-color);
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.pre {
  margin: 0;
  padding: $space-2;
  background: var(--el-fill-color-light);
  border-radius: $radius-sm;
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 11px;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.loading { color: var(--el-text-color-secondary); }

.pipeline-detail {
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.pipeline-field {
  display: flex;
  align-items: baseline;
  gap: $space-2;
  font-size: 12px;
  .field-label {
    color: var(--el-text-color-secondary);
    min-width: 70px;
  }
  code {
    font-family: 'JetBrains Mono', Menlo, monospace;
    &.kept { color: var(--el-color-success); }
    &.removed { color: var(--el-color-danger); }
  }
}

.expressor-compare {
  display: flex;
  gap: $space-3;
  .compare-col {
    flex: 1;
    min-width: 0;
  }
}

.empty {
  color: var(--el-text-color-secondary);
  padding: $space-4;
  text-align: center;
}
</style>
