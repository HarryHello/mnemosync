<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { listMemories, deleteMemory, getRelationship } from '@/api/client'
import type { Memory, Relationship } from '@/types/api'

const memories = ref<Memory[]>([])
const total = ref(0)
const loading = ref(false)
const selectedMemory = ref<Memory | null>(null)
const relationship = ref<Relationship | null>(null)

async function loadMemories() {
  loading.value = true
  try {
    const result = await listMemories()
    memories.value = result.items
    total.value = result.total
  } catch (e) {
    console.error('Failed to load memories:', e)
  } finally {
    loading.value = false
  }
}

async function loadRelationship() {
  try {
    relationship.value = await getRelationship()
  } catch (e) {
    console.error('Failed to load relationship:', e)
  }
}

async function handleDelete(memory: Memory) {
  if (!confirm(`确定删除记忆? "${memory.content.slice(0, 50)}..."`)) return
  await deleteMemory(memory.id)
  await loadMemories()
}

function selectMemory(memory: Memory) {
  selectedMemory.value = selectedMemory.value?.id === memory.id ? null : memory
}

function getMemoryTypeColor(type: string): string {
  switch (type) {
    case 'permanent': return '#4caf50'
    case 'normal': return '#2196f3'
    case 'fading': return '#ff9800'
    default: return '#999'
  }
}

onMounted(() => {
  loadMemories()
  loadRelationship()
})
</script>

<template>
  <div class="memories-view">
    <div class="header">
      <h1>Memories</h1>
      <button @click="loadMemories" :disabled="loading">
        {{ loading ? 'Loading...' : 'Refresh' }}
      </button>
    </div>

    <!-- Relationship Card -->
    <div class="relationship-card" v-if="relationship">
      <h2>Relationship Status</h2>
      <div class="stats">
        <div class="stat">
          <label>Intimacy</label>
          <div class="stat-value">{{ (relationship.intimacy * 100).toFixed(0) }}%</div>
          <div class="stat-bar">
            <div class="stat-fill" :style="{ width: `${relationship.intimacy * 100}%` }"></div>
          </div>
        </div>
        <div class="stat">
          <label>Trust</label>
          <div class="stat-value">{{ (relationship.trust * 100).toFixed(0) }}%</div>
          <div class="stat-bar">
            <div class="stat-fill trust" :style="{ width: `${relationship.trust * 100}%` }"></div>
          </div>
        </div>
        <div class="stat" v-if="relationship.relationship_type">
          <label>Type</label>
          <div class="stat-value">{{ relationship.relationship_type }}</div>
        </div>
      </div>
    </div>

    <div class="content">
      <div class="memory-list">
        <div
          v-for="memory in memories"
          :key="memory.id"
          class="memory-item"
          :class="{ selected: selectedMemory?.id === memory.id }"
          @click="selectMemory(memory)"
        >
          <div class="memory-header">
            <span class="type-badge" :style="{ background: getMemoryTypeColor(memory.memory_type) }">
              {{ memory.memory_type }}
            </span>
            <span class="importance">Importance: {{ memory.importance.toFixed(2) }}</span>
          </div>
          <div class="memory-content">{{ memory.content }}</div>
          <div class="memory-meta">
            <span>Accessed: {{ memory.access_count }} times</span>
            <span>Decay: {{ memory.decay_rate.toFixed(3) }}</span>
          </div>

          <div v-if="selectedMemory?.id === memory.id" class="memory-detail">
            <div class="detail-row">
              <label>ID:</label>
              <span>{{ memory.id }}</span>
            </div>
            <div class="detail-row">
              <label>Created:</label>
              <span>{{ new Date(memory.created_at).toLocaleString() }}</span>
            </div>
            <div class="detail-row" v-if="memory.last_accessed_at">
              <label>Last Accessed:</label>
              <span>{{ new Date(memory.last_accessed_at).toLocaleString() }}</span>
            </div>
            <div class="detail-row">
              <label>Source User:</label>
              <span>{{ memory.source_user }}</span>
            </div>
            <button class="danger" @click.stop="handleDelete(memory)">Delete</button>
          </div>
        </div>
      </div>
    </div>

    <div class="summary">
      Total: {{ total }} memories
    </div>
  </div>
</template>

<style scoped>
.memories-view {
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

.relationship-card {
  background: #f8f9fa;
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 20px;
}

.relationship-card h2 {
  margin: 0 0 16px 0;
  font-size: 18px;
}

.stats {
  display: flex;
  gap: 30px;
}

.stat {
  flex: 1;
}

.stat label {
  display: block;
  font-size: 13px;
  color: #666;
  margin-bottom: 4px;
}

.stat-value {
  font-size: 20px;
  font-weight: bold;
  margin-bottom: 8px;
}

.stat-bar {
  height: 8px;
  background: #e0e0e0;
  border-radius: 4px;
  overflow: hidden;
}

.stat-fill {
  height: 100%;
  background: #4caf50;
  border-radius: 4px;
  transition: width 0.3s;
}

.stat-fill.trust {
  background: #2196f3;
}

.content {
  display: flex;
  gap: 20px;
}

.memory-list {
  flex: 1;
  border: 1px solid #eee;
  border-radius: 8px;
  overflow: hidden;
}

.memory-item {
  border-bottom: 1px solid #eee;
  padding: 16px;
  cursor: pointer;
}

.memory-item:last-child {
  border-bottom: none;
}

.memory-item:hover {
  background: #f5f5f5;
}

.memory-item.selected {
  background: #e3f2fd;
}

.memory-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.type-badge {
  font-size: 11px;
  color: white;
  padding: 2px 8px;
  border-radius: 10px;
  text-transform: uppercase;
}

.importance {
  color: #666;
  font-size: 13px;
}

.memory-content {
  font-size: 14px;
  line-height: 1.5;
  margin-bottom: 8px;
}

.memory-meta {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: #999;
}

.memory-detail {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #eee;
}

.detail-row {
  display: flex;
  margin-bottom: 8px;
}

.detail-row label {
  width: 120px;
  color: #666;
}

button.danger {
  margin-top: 12px;
  color: #f44336;
  border-color: #f44336;
  background: white;
  padding: 6px 12px;
  border-radius: 4px;
  cursor: pointer;
}

button.danger:hover {
  background: #ffebee;
}

.summary {
  margin-top: 16px;
  text-align: center;
  color: #666;
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
</style>
