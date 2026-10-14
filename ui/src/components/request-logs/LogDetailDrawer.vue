<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import type { HttpLog, HttpLogTimelineResponse } from '@/types/api'
import { formatDate } from '@/utils/format'

const props = defineProps<{
  modelValue: boolean
  timeline: HttpLogTimelineResponse | null
  detailLoading: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// items[0] = 下游请求, 其余 = 管线中各 agent 的上游调用 (按发生顺序)
const downstream = computed<HttpLog | null>(
  () => props.timeline?.items[0] ?? null,
)
const hops = computed<HttpLog[]>(() => props.timeline?.items.slice(1) ?? [])

function hopTitle(h: HttpLog): string {
  const agent = h.agent ? `[${h.agent}] ` : ''
  return `${agent}${h.method} ${h.path}`
}

function statusType(code: number | null): 'success' | 'warning' | 'danger' | 'info' {
  if (code == null) return 'info'
  if (code >= 500) return 'danger'
  if (code >= 400) return 'warning'
  if (code >= 200) return 'success'
  return 'info'
}

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms.toFixed(1)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

function pretty(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}

async function copyBody(v: unknown) {
  try {
    await navigator.clipboard.writeText(pretty(v))
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}
</script>

<template>
  <el-drawer v-model="visible" title="请求链路" size="720px" direction="rtl">
    <div v-loading="detailLoading" class="detail">
      <template v-if="downstream">
        <!-- 下游请求 -->
        <div class="phase-head">
          <span class="phase-name">下游请求</span>
          <span class="mono muted">{{ formatDate(downstream.created_at) }}</span>
        </div>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="路径">
            <span class="mono">{{ downstream.path }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="客户端">
            <span class="mono">{{ downstream.client_ip || '—' }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <div class="section-title">
          请求体
          <el-button link size="small" @click="copyBody(downstream.request_body)">复制</el-button>
        </div>
        <pre class="code-block">{{ pretty(downstream.request_body) || '—' }}</pre>

        <!-- 管线: 各 agent 的上游调用 -->
        <div class="phase-head pipeline-head">
          <span class="phase-name">管线 · 上游调用 ({{ hops.length }})</span>
        </div>
        <el-empty
          v-if="hops.length === 0"
          description="该请求没有上游调用记录 (早于 beta.27 的日志无链路数据)"
          :image-size="60"
        />
        <template v-for="(h, i) in hops" :key="h.id">
          <div class="hop">
            <div class="hop-head">
              <span class="hop-index">{{ i + 1 }}</span>
              <span class="mono hop-url" :title="h.path">{{ hopTitle(h) }}</span>
              <el-tag :type="statusType(h.response_status)" size="small">
                {{ h.response_status ?? '—' }}
              </el-tag>
              <span class="mono muted">{{ fmtDuration(h.duration_ms) }}</span>
            </div>
            <div v-if="h.request_body != null" class="hop-body">
              <div class="section-title">
                请求体
                <el-button link size="small" @click="copyBody(h.request_body)">复制</el-button>
              </div>
              <pre class="code-block small">{{ pretty(h.request_body) }}</pre>
            </div>
            <div v-if="h.response_body != null" class="hop-body">
              <div class="section-title">
                响应体
                <el-button link size="small" @click="copyBody(h.response_body)">复制</el-button>
              </div>
              <pre class="code-block small">{{ pretty(h.response_body) }}</pre>
            </div>
          </div>
        </template>

        <!-- 下游响应 -->
        <div class="phase-head">
          <span class="phase-name">下游响应</span>
          <el-tag :type="statusType(downstream.response_status)" size="small">
            {{ downstream.response_status ?? '—' }}
          </el-tag>
          <span class="mono muted">{{ fmtDuration(downstream.duration_ms) }}</span>
        </div>
        <div class="section-title">
          响应体
          <el-button link size="small" @click="copyBody(downstream.response_body)">复制</el-button>
        </div>
        <pre class="code-block">{{ pretty(downstream.response_body) || '—' }}</pre>
      </template>
    </div>
  </el-drawer>
</template>

<style lang="scss" scoped>
.detail {
  padding: 0 $space-2 $space-4;
}

.phase-head {
  display: flex;
  gap: $space-2;
  align-items: center;
  padding: $space-2 0;
  margin-top: $space-2;
  border-bottom: 1px solid var(--el-border-color-lighter);

  &:first-child {
    margin-top: 0;
  }
}

.phase-name {
  font-size: 14px;
  font-weight: 600;
}

.pipeline-head {
  margin-top: $space-4;
}

.section-title {
  margin: $space-3 0 $space-1;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);

  .el-button {
    margin-left: $space-1;
  }
}

.hop {
  padding: $space-2;
  margin: $space-2 0;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-md;
}

.hop-head {
  display: flex;
  gap: $space-2;
  align-items: center;
  min-width: 0;
}

.hop-index {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  font-size: 12px;
  line-height: 20px;
  color: var(--el-color-primary);
  text-align: center;
  background: var(--el-color-primary-light-9);
  border-radius: 50%;
}

.hop-url {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  white-space: nowrap;
}

.hop-body .code-block {
  max-height: 260px;
}

.code-block {
  max-height: 360px;
  padding: $space-3;
  margin: 0;
  overflow: auto;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
  white-space: pre-wrap;
  background: var(--el-fill-color-light);
  border-radius: $radius-sm;

  &.small {
    font-size: 11px;
  }
}

.mono {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
}

.muted {
  color: var(--el-text-color-secondary);
}
</style>
