<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getRelationship,
  getRelationshipAudit,
  updateRelationship,
} from '@/api/client'
import type {
  Relationship,
  RelationshipAuditEntry,
  RelationshipUpdateBody,
} from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'
import RelationshipEditDialog from '@/components/relationships/RelationshipEditDialog.vue'
import RelationshipAuditTable from '@/components/relationships/RelationshipAuditTable.vue'

const rel = ref<Relationship | null>(null)
const audit = ref<RelationshipAuditEntry[]>([])
const loading = ref(false)
const auditLoading = ref(false)
const errMsg = ref<string | null>(null)
const userId = ref('default')

const editDialogVisible = ref(false)
const editSaving = ref(false)

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

type TagType = 'primary' | 'success' | 'warning' | 'danger' | 'info'
function levelType(v: number): TagType {
  const p = clamp01(v)
  if (p >= 0.65) return 'success'
  if (p >= 0.4) return 'primary'
  if (p >= 0.2) return 'warning'
  return 'danger'
}

type ProgressStatus = '' | 'success' | 'warning' | 'exception'
function levelStatus(v: number): ProgressStatus {
  const p = clamp01(v)
  if (p >= 0.65) return 'success'
  if (p >= 0.4) return ''
  if (p >= 0.2) return 'warning'
  return 'exception'
}

function fmtDate(s: string | null): string {
  if (!s) return '—'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('zh-CN', { hour12: false })
}

const fieldLabels: Record<string, string> = {
  persona_addressing: '人格自称',
  user_addressing: '用户称呼',
  context: '关系背景',
}

async function refresh() {
  loading.value = true
  errMsg.value = null
  try {
    rel.value = await getRelationship(userId.value || 'default')
    await refreshAudit()
  } catch (err) {
    rel.value = null
    errMsg.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function refreshAudit() {
  auditLoading.value = true
  try {
    const resp = await getRelationshipAudit(userId.value || 'default', 20)
    audit.value = resp.items
  } catch (err) {
    audit.value = []
    console.warn('audit load failed', err)
  } finally {
    auditLoading.value = false
  }
}

function openEditDialog() {
  if (!rel.value) return
  editDialogVisible.value = true
}

async function submitEdit(payload: any) {
  if (!rel.value) return
  const body: RelationshipUpdateBody = { ...payload, user_id: userId.value || 'default' }
  editSaving.value = true
  try {
    rel.value = await updateRelationship(body)
    await refreshAudit()
    editDialogVisible.value = false
    ElMessage.success('已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    editSaving.value = false
  }
}

async function revertToAudit(entry: RelationshipAuditEntry) {
  try {
    await ElMessageBox.confirm(
      `回退 ${fieldLabels[entry.field_name] ?? entry.field_name} 到旧值 "${
        entry.old_value ?? '(空)'
      }"?`,
      '确认回退',
      { type: 'warning', confirmButtonText: '回退', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  const body: RelationshipUpdateBody = {
    reason: `回退 audit #${entry.id}: ${fieldLabels[entry.field_name] ?? entry.field_name} → 旧值`,
    user_id: userId.value || 'default',
    [entry.field_name]: entry.old_value,
  }
  try {
    rel.value = await updateRelationship(body)
    await refreshAudit()
    ElMessage.success('已回退')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

onMounted(refresh)
</script>

<template>
  <div class="page-container">
    <PageHeader
      title="关系状态"
      subtitle="Mnemosync 与用户之间的亲密度、信任度、称呼与关系背景。单人格版本 persona_id=default, 后续多人格再扩展。"
    >
      <template #actions>
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
      </template>
    </PageHeader>

    <el-alert
      v-if="errMsg"
      :title="'加载失败: ' + errMsg"
      type="error"
      :closable="false"
      show-icon
      class="mb"
    />

    <el-alert
      v-if="rel && !rel.updated_at"
      title="关系尚未建立"
      description="新装或刚重置后, 此用户还没有与人格交互过。下次对话时会自动创建关系记录, 当前称呼沿用安装基线 (TOML)。"
      type="info"
      :closable="false"
      show-icon
      class="mb"
    />

    <div v-loading="loading">
      <div v-if="rel" class="grid">
        <el-card class="metric">
          <template #header>
            <div class="metric-head">
              <span>亲密度</span>
              <el-tag size="small" :type="levelType(rel.intimacy)">
                {{ levelText(rel.intimacy) }}
              </el-tag>
            </div>
          </template>
          <div class="metric-value mono">{{ rel.intimacy.toFixed(3) }}</div>
          <el-progress
            :percentage="intimacyPct"
            :stroke-width="10"
            :status="levelStatus(rel.intimacy)"
          />
          <div class="metric-hint">
            与用户互动的亲密程度, 由对话行为与情感极性驱动累积。
          </div>
        </el-card>

        <el-card class="metric">
          <template #header>
            <div class="metric-head">
              <span>信任度</span>
              <el-tag size="small" :type="levelType(rel.trust)">
                {{ levelText(rel.trust) }}
              </el-tag>
            </div>
          </template>
          <div class="metric-value mono">{{ rel.trust.toFixed(3) }}</div>
          <el-progress
            :percentage="trustPct"
            :stroke-width="10"
            :status="levelStatus(rel.trust)"
          />
          <div class="metric-hint">
            信任等级影响记忆可见性 (CONFIDENTIAL / FRIENDS_ONLY 门槛)。
          </div>
        </el-card>

        <el-card class="addressing">
          <template #header>
            <div class="addressing-head">
              <span>当前称呼与关系背景</span>
              <el-button size="small" type="primary" @click="openEditDialog">
                <el-icon><Edit /></el-icon>
                <span>编辑</span>
              </el-button>
            </div>
          </template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="人格自称">
              <span class="mono">{{ rel.persona_addressing }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="用户称呼">
              <span class="mono">{{ rel.user_addressing }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="关系背景">
              <span class="context">{{ rel.context }}</span>
            </el-descriptions-item>
          </el-descriptions>
          <div class="metric-hint">
            这些值优先取自 relationships 表, 未被覆盖时沿用 TOML 安装基线。
            关系分析 Agent 可在对话中自动更新它们并写入下方变更历史。
          </div>
        </el-card>

        <el-card class="info">
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

        <el-card class="audit">
          <template #header>
            <div class="addressing-head">
              <span>变更历史 (最近 20 条)</span>
              <el-button size="small" text @click="refreshAudit" :loading="auditLoading">
                <el-icon><Refresh /></el-icon>
                <span>刷新</span>
              </el-button>
            </div>
          </template>
          <RelationshipAuditTable
            :items="audit"
            :loading="auditLoading"
            @refresh="refreshAudit"
            @revert="revertToAudit"
          />
        </el-card>
      </div>

      <el-empty
        v-else-if="!loading && !errMsg"
        description="尚无该用户的关系记录"
      />
    </div>

    <RelationshipEditDialog
      v-model="editDialogVisible"
      :submitting="editSaving"
      :relationship="rel"
      @submit="submitEdit"
    />
  </div>
</template>

<style lang="scss" scoped>
.mb {
  margin-bottom: $space-4;
}

.grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: $space-4;

  @include respond-to(md) {
    grid-template-columns: repeat(2, 1fr);

    .info,
    .addressing,
    .audit {
      grid-column: 1 / -1;
    }
  }
}

.metric-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.addressing-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: $space-2;
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

.notes,
.context {
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
