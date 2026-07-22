<script setup lang="ts">
import { computed } from 'vue'
import type { HttpLog } from '@/types/api'

const props = defineProps<{
  modelValue: boolean
  detail: HttpLog | null
  detailLoading: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

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

function fmtDate(s: string | null): string {
  if (!s) return '—'
  return new Date(s + 'Z').toLocaleString('zh-CN', { hour12: false })
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
</script>

<template>
  <el-drawer v-model="visible" title="日志详情" size="640px" direction="rtl">
    <div v-loading="detailLoading" class="detail">
      <template v-if="detail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="时间">
            <span class="mono">{{ fmtDate(detail.created_at) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="方法">
            <el-tag size="small" class="mono">{{ detail.method }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="路径">
            <span class="mono">{{ detail.path }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="Query">
            <span class="mono">{{ detail.query_params || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="状态码">
            <el-tag :type="statusType(detail.response_status)" size="small">
              {{ detail.response_status ?? '—' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="耗时">
            <span class="mono">{{ fmtDuration(detail.duration_ms) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="客户端">
            <span class="mono">{{ detail.client_ip || '—' }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <div class="section-title">请求头</div>
        <pre class="code-block">{{ pretty(detail.request_headers) }}</pre>

        <div class="section-title">请求体</div>
        <pre class="code-block">{{ pretty(detail.request_body) || '—' }}</pre>

        <div class="section-title">响应体</div>
        <pre class="code-block">{{ pretty(detail.response_body) || '—' }}</pre>
      </template>
    </div>
  </el-drawer>
</template>

<style lang="scss" scoped>
.detail {
  padding: 0 $space-2 $space-4;
}

.section-title {
  margin: $space-4 0 $space-2;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.code-block {
  background: var(--el-fill-color-light);
  border-radius: $radius-sm;
  padding: $space-3;
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow-y: auto;
  margin: 0;
}
</style>
