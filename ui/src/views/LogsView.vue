<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { listLogs, clearLogs } from '@/api/client'
import type { HttpLog } from '@/types/api'

const logs = ref<HttpLog[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const loading = ref(false)
const selectedLog = ref<HttpLog | null>(null)

// 筛选
const filterMethod = ref('')
const filterPath = ref('')
const filterStatus = ref('')

async function loadLogs() {
  loading.value = true
  try {
    const result = await listLogs({
      page: page.value,
      page_size: pageSize,
      method: filterMethod.value || undefined,
      path: filterPath.value || undefined,
      status: filterStatus.value ? Number(filterStatus.value) : undefined,
    })
    logs.value = result.items
    total.value = result.total
  } catch (e) {
    console.error('Failed to load logs:', e)
  } finally {
    loading.value = false
  }
}

async function handleClear() {
  if (!confirm('确定清空所有日志?')) return
  await clearLogs()
  await loadLogs()
}

function selectLog(log: HttpLog) {
  selectedLog.value = selectedLog.value?.id === log.id ? null : log
}

const totalPages = computed(() => Math.ceil(total.value / pageSize))

function formatDuration(ms: number | null): string {
  if (ms === null) return '-'
  if (ms < 1) return '<1ms'
  return `${Math.round(ms)}ms`
}

function formatJson(obj: unknown): string {
  if (obj === null || obj === undefined) return '-'
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(obj)
  }
}

onMounted(loadLogs)
</script>

<template>
  <div class="logs-view">
    <div class="header">
      <h1>HTTP Logs</h1>
      <div class="actions">
        <button @click="loadLogs" :disabled="loading">
          {{ loading ? 'Loading...' : 'Refresh' }}
        </button>
        <button @click="handleClear" class="danger">Clear All</button>
      </div>
    </div>

    <div class="filters">
      <select v-model="filterMethod" @change="loadLogs">
        <option value="">All Methods</option>
        <option value="GET">GET</option>
        <option value="POST">POST</option>
        <option value="DELETE">DELETE</option>
      </select>

      <input v-model="filterPath" placeholder="Filter path..." @keyup.enter="loadLogs" />

      <select v-model="filterStatus" @change="loadLogs">
        <option value="">All Status</option>
        <option value="200">200</option>
        <option value="400">400</option>
        <option value="401">401</option>
        <option value="404">404</option>
        <option value="500">500</option>
      </select>
    </div>

    <div class="log-list">
      <div
        v-for="log in logs"
        :key="log.id"
        class="log-item"
        :class="{ selected: selectedLog?.id === log.id }"
        @click="selectLog(log)"
      >
        <div class="log-header">
          <span class="method" :class="log.method.toLowerCase()">{{ log.method }}</span>
          <span class="path">{{ log.path }}</span>
          <span class="status" :class="getStatusClass(log.response_status)">{{ log.response_status || '-' }}</span>
          <span class="duration">{{ formatDuration(log.duration_ms) }}</span>
          <span class="time">{{ new Date(log.created_at).toLocaleTimeString() }}</span>
        </div>

        <div v-if="selectedLog?.id === log.id" class="log-detail">
          <div class="detail-section">
            <h3>Request Headers</h3>
            <pre>{{ formatJson(log.request_headers) }}</pre>
          </div>
          <div class="detail-section">
            <h3>Request Body</h3>
            <pre>{{ formatJson(log.request_body) }}</pre>
          </div>
          <div class="detail-section">
            <h3>Response Body</h3>
            <pre>{{ formatJson(log.response_body) }}</pre>
          </div>
        </div>
      </div>
    </div>

    <div class="pagination" v-if="totalPages > 1">
      <button @click="page--; loadLogs()" :disabled="page <= 1">Prev</button>
      <span>Page {{ page }} / {{ totalPages }}</span>
      <button @click="page++; loadLogs()" :disabled="page >= totalPages">Next</button>
    </div>
  </div>
</template>

<script lang="ts">
function getStatusClass(status: number | null): string {
  if (!status) return ''
  if (status < 300) return 'success'
  if (status < 400) return 'redirect'
  if (status < 500) return 'client-error'
  return 'server-error'
}
</script>

<style scoped>
.logs-view {
  padding: 20px;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.header h1 {
  margin: 0;
  font-size: 24px;
}

.actions {
  display: flex;
  gap: 10px;
}

.filters {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
}

.filters input,
.filters select {
  padding: 8px 12px;
  border: 1px solid #ccc;
  border-radius: 4px;
}

.log-list {
  border: 1px solid #eee;
  border-radius: 8px;
  overflow: hidden;
}

.log-item {
  border-bottom: 1px solid #eee;
  cursor: pointer;
}

.log-item:last-child {
  border-bottom: none;
}

.log-item:hover {
  background: #f5f5f5;
}

.log-item.selected {
  background: #e3f2fd;
}

.log-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
}

.method {
  font-weight: bold;
  font-family: monospace;
  min-width: 60px;
}

.method.get { color: #2196f3; }
.method.post { color: #4caf50; }
.method.delete { color: #f44336; }

.path {
  flex: 1;
  font-family: monospace;
  font-size: 14px;
}

.status {
  font-family: monospace;
  font-weight: bold;
}

.status.success { color: #4caf50; }
.status.redirect { color: #ff9800; }
.status.client-error { color: #f44336; }
.status.server-error { color: #9c27b0; }

.duration {
  color: #666;
  font-size: 13px;
  min-width: 60px;
  text-align: right;
}

.time {
  color: #999;
  font-size: 13px;
  min-width: 80px;
}

.log-detail {
  padding: 12px;
  background: #fafafa;
  border-top: 1px solid #eee;
}

.detail-section {
  margin-bottom: 12px;
}

.detail-section:last-child {
  margin-bottom: 0;
}

.detail-section h3 {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: #666;
}

.detail-section pre {
  margin: 0;
  padding: 12px;
  background: #fff;
  border: 1px solid #eee;
  border-radius: 4px;
  overflow-x: auto;
  font-size: 12px;
  max-height: 300px;
  overflow-y: auto;
}

.pagination {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 20px;
  margin-top: 20px;
  padding: 12px;
}

button {
  padding: 8px 16px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
}

button:hover:not(:disabled) {
  background: #f5f5f5;
}

button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

button.danger {
  color: #f44336;
  border-color: #f44336;
}

button.danger:hover {
  background: #ffebee;
}
</style>
