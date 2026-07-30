<script setup lang="ts">
import { computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useNotificationsStore } from '@/stores/notifications'
import type { Notification } from '@/types/api'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const store = useNotificationsStore()

const visible = computed<boolean>({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

watch(
  () => props.modelValue,
  (open) => {
    if (open) void store.fetchList({ page: 1 })
  },
)

const LEVEL_LABEL: Record<string, string> = {
  info: '信息',
  warning: '警告',
  error: '错误',
}

function levelTagType(level: string): 'info' | 'warning' | 'danger' {
  if (level === 'error') return 'danger'
  if (level === 'warning') return 'warning'
  return 'info'
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

async function onMarkOne(n: Notification) {
  if (n.read_at) return
  try {
    await store.markRead(n.id)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function onMarkAll() {
  try {
    await store.markAllRead()
    ElMessage.success('已全部标记已读')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function onDeleteRead() {
  if (store.readCount === 0) return
  try {
    await ElMessageBox.confirm(
      `将删除 ${store.readCount} 条已读通知, 未读不受影响。`,
      '删除已读',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const n = await store.removeRead()
    ElMessage.success(`已删除 ${n} 条`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function onRefresh() {
  try {
    await store.fetchList({ page: 1 })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}
</script>

<template>
  <el-drawer
    v-model="visible"
    title="通知中心"
    direction="rtl"
    size="480px"
    :append-to-body="true"
  >
    <template #header>
      <div class="drawer-header">
        <span class="title">通知中心</span>
        <span v-if="store.unreadCount > 0" class="unread-hint">{{ store.unreadCount }} 条未读</span>
      </div>
    </template>

    <div class="drawer-toolbar">
      <el-button size="small" :disabled="store.loading" @click="onRefresh">刷新</el-button>
      <el-button
        size="small"
        type="primary"
        plain
        :disabled="store.unreadCount === 0"
        @click="onMarkAll"
      >全部已读</el-button>
      <el-button
        size="small"
        type="danger"
        plain
        :disabled="store.readCount === 0"
        @click="onDeleteRead"
      >删除已读</el-button>
    </div>

    <div v-loading="store.loading" class="drawer-body">
      <el-empty
        v-if="!store.loading && store.items.length === 0"
        description="暂无通知"
      />
      <ul v-else class="notif-list">
        <li
          v-for="n in store.items"
          :key="n.id"
          class="notif-item"
          :class="{ 'is-read': !!n.read_at }"
        >
          <span class="time">{{ formatTime(n.created_at) }}</span>

          <div class="row-top">
            <span v-if="!n.read_at" class="dot" aria-hidden="true" />
            <el-tag :type="levelTagType(n.level)" size="small" effect="light">
              {{ LEVEL_LABEL[n.level] ?? n.level }}
            </el-tag>
            <span class="cat">{{ n.category }}</span>
          </div>

          <div class="title-line">{{ n.title }}</div>
          <div v-if="n.message" class="message">{{ n.message }}</div>
          <div v-if="n.meta" class="meta">
            <span v-for="(v, k) in n.meta" :key="k" class="meta-pair">
              <b>{{ k }}</b>: {{ typeof v === 'object' ? JSON.stringify(v) : String(v) }}
            </span>
          </div>

          <div class="row-actions">
            <el-button
              v-if="!n.read_at"
              size="small"
              type="primary"
              plain
              @click.stop="onMarkOne(n)"
            >标为已读</el-button>
          </div>
        </li>
      </ul>
    </div>
  </el-drawer>
</template>

<style lang="scss" scoped>
.drawer-header {
  display: flex;
  align-items: baseline;
  gap: $space-2;
}

.title {
  font-weight: 600;
  font-size: 15px;
}

.unread-hint {
  font-size: 12px;
  color: var(--el-color-primary);
}

.drawer-toolbar {
  display: flex;
  gap: $space-2;
  padding-bottom: $space-2;
  border-bottom: 1px solid var(--el-border-color-lighter);
  margin-bottom: $space-2;
}

.drawer-body {
  min-height: 200px;
}

.notif-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.notif-item {
  position: relative;
  padding: $space-2 $space-3;
  padding-right: 150px; /* 让出右上角时间戳的空间 */
  padding-bottom: 36px; /* 让出右下角已读按钮的空间 */
  border-radius: $radius-sm;
  background: var(--el-fill-color-lighter);
  transition: background 0.15s ease;

  &:hover {
    background: var(--el-fill-color);
  }

  &.is-read {
    color: var(--el-text-color-placeholder);
    background: var(--el-fill-color-blank);
    padding-bottom: $space-2; /* 已读时不再有按钮, 恢复紧凑 */

    .title-line,
    .message,
    .cat,
    .time,
    .meta {
      color: var(--el-text-color-placeholder);
    }

    :deep(.el-tag) {
      filter: grayscale(0.7);
      opacity: 0.7;
    }
  }
}

.row-top {
  display: flex;
  align-items: center;
  gap: $space-2;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--el-color-primary);
  flex: 0 0 auto;
}

.cat {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 11px;
}

.time {
  position: absolute;
  top: $space-2;
  right: $space-3;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.row-actions {
  position: absolute;
  right: $space-3;
  bottom: $space-2;
}

.title-line {
  margin-top: $space-1;
  font-weight: 600;
  color: var(--el-text-color-primary);
  font-size: 13px;
}

.message {
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
  word-break: break-word;
}

.meta {
  margin-top: $space-1;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  display: flex;
  flex-wrap: wrap;
  gap: $space-2;
}

.meta-pair b {
  font-weight: 600;
  margin-right: 2px;
}
</style>
