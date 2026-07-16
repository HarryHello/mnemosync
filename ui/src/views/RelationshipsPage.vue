<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getRelationship } from '@/api/client'
import type { Relationship } from '@/types/api'

const rel = ref<Relationship | null>(null)
const loading = ref(false)
const errMsg = ref<string | null>(null)
const userId = ref('default')

const intimacyPct = computed(() =>
  rel.value ? Math.round(clamp01(rel.value.intimacy) * 100) : 0,
)
const trustPct = computed(() =>
  rel.value ? Math.round(clamp01(rel.value.trust) * 100) : 0,
)

function clamp01(v: number): number {
  if (Number.isNaN(v)) return 0
  return Math.max(0, Math.min(1, v))
}

function levelText(v: number): string {
  const p = clamp01(v)
  if (p >= 0.85) return '极高'
  if (p >= 0.65) return '高'
  if (p >= 0.4) return '中等'
  if (p >= 0.2) return '低'
  return '陌生'
}

function fmtDate(s: string | null): string {
  if (!s) return '—'
  return new Date(s).toLocaleString('zh-CN', { hour12: false })
}

async function refresh() {
  loading.value = true
  errMsg.value = null
  try {
    rel.value = await getRelationship(userId.value || 'default')
  } catch (err) {
    rel.value = null
    errMsg.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="page-container">
    <div class="page-head">
      <div>
        <h2 class="page-title">关系状态</h2>
        <p class="page-subtitle">
          Mnemosync 与用户之间的亲密度、信任度与关系类型。
          单人格版本 <span class="mono">persona_id=default</span>, 后续多人格再扩展。
        </p>
      </div>
      <div class="head-actions">
        <el-input
          v-model="userId"
          placeholder="user_id (默认 default)"
          clearable
          style="width: 220px"
          @keyup.enter="refresh"
        >
          <template #prefix>
            <el-icon><User /></el-icon>
          </template>
        </el-input>
        <el-button :loading="loading" type="primary" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>加载</span>
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="errMsg"
      :title="'加载失败: ' + errMsg"
      type="error"
      :closable="false"
      show-icon
      class="mb"
    />

    <div v-loading="loading">
      <div v-if="rel" class="grid">
        <el-card shadow="hover" class="metric">
          <template #header>
            <div class="metric-head">
              <span>亲密度</span>
              <el-tag size="small" type="warning">{{ levelText(rel.intimacy) }}</el-tag>
            </div>
          </template>
          <div class="metric-value mono">{{ rel.intimacy.toFixed(3) }}</div>
          <el-progress
            :percentage="intimacyPct"
            :stroke-width="10"
            status="warning"
          />
          <div class="metric-hint">
            与用户互动的亲密程度, 由对话行为与情感极性驱动累积。
          </div>
        </el-card>

        <el-card shadow="hover" class="metric">
          <template #header>
            <div class="metric-head">
              <span>信任度</span>
              <el-tag size="small" type="success">{{ levelText(rel.trust) }}</el-tag>
            </div>
          </template>
          <div class="metric-value mono">{{ rel.trust.toFixed(3) }}</div>
          <el-progress
            :percentage="trustPct"
            :stroke-width="10"
            status="success"
          />
          <div class="metric-hint">
            信任等级影响记忆可见性 (CONFIDENTIAL / FRIENDS_ONLY 门槛)。
          </div>
        </el-card>

        <el-card shadow="never" class="info">
          <template #header>
            <span>关系元数据</span>
          </template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="人格 ID">
              <span class="mono">{{ rel.persona_id }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="用户 ID">
              <span class="mono">{{ rel.user_id }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="关系类型">
              <el-tag v-if="rel.relationship_type" size="small">
                {{ rel.relationship_type }}
              </el-tag>
              <span v-else class="muted">未定义</span>
            </el-descriptions-item>
            <el-descriptions-item label="最近活跃">
              <span class="mono">{{ fmtDate(rel.updated_at) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="备注">
              <span v-if="rel.notes" class="notes">{{ rel.notes }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </div>

      <el-empty
        v-else-if="!loading && !errMsg"
        description="尚无该用户的关系记录"
      />
    </div>
  </div>
</template>

<style lang="scss" scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $space-4;
  margin-bottom: $space-4;
  flex-wrap: wrap;
}

.head-actions {
  display: flex;
  gap: $space-2;
  align-items: center;
}

.mb {
  margin-bottom: $space-4;
}

.grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: $space-4;

  @include respond-to(md) {
    grid-template-columns: repeat(2, 1fr);

    .info {
      grid-column: 1 / -1;
    }
  }
}

.metric-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.metric-value {
  font-size: 28px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-bottom: $space-2;
}

.metric-hint {
  margin-top: $space-2;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.muted {
  color: var(--el-text-color-secondary);
}

.notes {
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
