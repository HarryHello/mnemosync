<script setup lang="ts">
import { computed } from 'vue'
import type { ConversationTurn } from '@/types/api'

const props = defineProps<{
  modelValue: boolean
  item: ConversationTurn | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

function roleTag(r: string): 'primary' | 'success' | 'info' {
  if (r === 'user') return 'primary'
  if (r === 'assistant') return 'success'
  return 'info'
}

function roleLabel(r: string): string {
  if (r === 'user') return '用户'
  if (r === 'assistant') return '助手'
  return r
}

function fmtDate(s: string | null): string {
  if (!s) return '—'
  return new Date(s).toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <el-drawer
    v-model="visible"
    :title="item ? `${roleLabel(item.role)} 消息 #${item.id}` : '详情'"
    size="50%"
  >
    <div v-if="item" class="detail-content">
      <div class="detail-meta">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="角色">
            <el-tag :type="roleTag(item.role)" size="small">
              {{ roleLabel(item.role) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="事件类型">{{ item.origin }}</el-descriptions-item>
          <el-descriptions-item label="说话者">
            {{ item.display_name || (item.role === 'assistant' ? '人格' : '未识别用户') }}
          </el-descriptions-item>
          <el-descriptions-item label="平台账号">
            <span class="mono muted">
              {{ item.source_frontend || '—' }}<template v-if="item.external_key"> · {{ item.external_key }}</template>
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="事件时间">
            <span class="mono muted">{{ fmtDate(item.ts) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="观察时间">
            <span class="mono muted">{{ fmtDate(item.observed_at) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="空间">
            <span class="mono muted">{{ item.space_id || '私聊 / 全局' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="空间序号">
            {{ item.committed_sequence ?? '—' }}
            <el-tag v-if="item.late_arrival" size="small" type="warning">迟到</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="Actor ID">
            <span class="mono muted">{{ item.actor_id || '未匹配' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="有效用户 ID">
            <span class="mono muted">{{ item.effective_user_id || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="请求 ID">
            <span class="mono muted">{{ item.request_id || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="平台事件 ID">
            <span class="mono muted">{{ item.external_event_id || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="事件指纹" :span="2">
            <span class="mono muted fingerprint">{{ item.event_fingerprint || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="token">{{ item.token_count }}</el-descriptions-item>
        </el-descriptions>
      </div>
      <div class="detail-text">
        <div class="detail-text-label">内容</div>
        <pre class="detail-pre">{{ item.content }}</pre>
      </div>
    </div>
  </el-drawer>
</template>

<style lang="scss" scoped>
.detail-content {
  display: flex;
  flex-direction: column;
  gap: $space-5;
}

.detail-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.detail-text-label {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.detail-pre {
  margin: 0;
  padding: $space-4;
  background: var(--el-fill-color-lighter);
  border-radius: $radius-md;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
  font-family: inherit;
  font-size: 14px;
  color: var(--el-text-color-primary);
  max-height: 60vh;
  overflow-y: auto;
}

.fingerprint {
  overflow-wrap: anywhere;
}

.muted {
  color: var(--el-text-color-secondary);
}
</style>
