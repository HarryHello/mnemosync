<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getRelationship,
  getRelationshipAudit,
  listRelationships,
  updateRelationship,
} from '@/api/client'
import type {
  ListRelationshipsParams,
} from '@/api/client'
import type { Relationship, RelationshipAuditEntry, RelationshipUpdateBody } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'
import RelationshipListTable from '@/components/relationships/RelationshipListTable.vue'
import RelationshipEditDialog from '@/components/relationships/RelationshipEditDialog.vue'
import RelationshipAuditTable from '@/components/relationships/RelationshipAuditTable.vue'

// ─── 列表状态 ────────────────────────────────

const listItems = ref<Relationship[]>([])
const listLoading = ref(false)
const listTotal = ref(0)
const listPage = ref(1)
const listPageSize = ref(20)
const listSortBy = ref('intimacy_score')
const listSortOrder = ref('desc')

// ─── 详情状态 ────────────────────────────────

const selectedUserId = ref<string | null>(null)
const rel = ref<Relationship | null>(null)
const audit = ref<RelationshipAuditEntry[]>([])
const detailLoading = ref(false)
const auditLoading = ref(false)
const errMsg = ref<string | null>(null)

const editDialogVisible = ref(false)
const editSaving = ref(false)

// ─── 计算属性 ────────────────────────────────

const selectedIdentity = computed(() => rel.value?.identity ?? null)
const selectedIdentityName = computed(() => {
  const identity = selectedIdentity.value
  const account = identity?.accounts[0]
  return identity?.name || account?.display_name || account?.external_key || rel.value?.user_id || '未知用户'
})

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

// ─── 列表刷新 ────────────────────────────────

async function refreshList() {
  listLoading.value = true
  try {
    const params: ListRelationshipsParams = {
      page: listPage.value,
      page_size: listPageSize.value,
      sort_by: listSortBy.value,
      sort_order: listSortOrder.value,
    }
    const res = await listRelationships(params)
    listItems.value = res.items
    listTotal.value = res.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
    listItems.value = []
    listTotal.value = 0
  } finally {
    listLoading.value = false
  }
}

// ─── 详情加载 ────────────────────────────────

async function loadDetail(userId: string) {
  selectedUserId.value = userId
  detailLoading.value = true
  errMsg.value = null
  try {
    rel.value = await getRelationship(userId)
    await refreshAudit()
  } catch (err) {
    rel.value = null
    errMsg.value = err instanceof Error ? err.message : String(err)
  } finally {
    detailLoading.value = false
  }
}

async function refreshAudit() {
  if (!selectedUserId.value) return
  auditLoading.value = true
  try {
    const resp = await getRelationshipAudit(selectedUserId.value, 20)
    audit.value = resp.items
  } catch (err) {
    audit.value = []
    console.warn('audit load failed', err)
  } finally {
    auditLoading.value = false
  }
}

// ─── 选中行 ────────────────────────────────

function onSelectRow(row: Relationship) {
  loadDetail(row.user_id)
}

function backToList() {
  selectedUserId.value = null
  rel.value = null
  audit.value = []
}

// ─── 编辑 ────────────────────────────────

function openEditDialog() {
  if (!rel.value) return
  editDialogVisible.value = true
}

async function submitEdit(payload: any) {
  if (!rel.value || !selectedUserId.value) return
  const body: RelationshipUpdateBody = { ...payload, user_id: selectedUserId.value }
  editSaving.value = true
  try {
    rel.value = await updateRelationship(body)
    await refreshAudit()
    editDialogVisible.value = false
    ElMessage.success('已保存')
    await refreshList()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    editSaving.value = false
  }
}

// ─── 回退 ────────────────────────────────

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
  if (!selectedUserId.value) return
  const body: RelationshipUpdateBody = {
    reason: `回退 audit #${entry.id}: ${fieldLabels[entry.field_name] ?? entry.field_name} → 旧值`,
    user_id: selectedUserId.value,
    [entry.field_name]: entry.old_value,
  }
  try {
    rel.value = await updateRelationship(body)
    await refreshAudit()
    ElMessage.success('已回退')
    await refreshList()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

// ─── 分页/排序变更 ──────────────────────────

function onPageChange(page: number) {
  listPage.value = page
  refreshList()
}

function onPageSizeChange(size: number) {
  listPageSize.value = size
  listPage.value = 1
  refreshList()
}

onMounted(refreshList)
</script>

<template>
  <div class="page-container">
    <PageHeader
      title="关系状态"
      subtitle="当前人格与各用户之间的亲密度、信任度、称呼与关系背景。选择一个用户查看详情或编辑。"
    >
      <template #actions>
        <el-button v-if="selectedUserId" :loading="listLoading" @click="backToList">
          <el-icon><Back /></el-icon>
          <span>返回列表</span>
        </el-button>
        <el-button :loading="listLoading" @click="refreshList">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </template>
    </PageHeader>

    <!-- 列表模式 -->
    <template v-if="!selectedUserId">
      <RelationshipListTable
        :items="listItems"
        :loading="listLoading"
        :total="listTotal"
        :page="listPage"
        :page-size="listPageSize"
        @select="onSelectRow"
        @update:page="onPageChange"
        @update:page-size="onPageSizeChange"
        @refresh="refreshList"
      />
    </template>

    <!-- 详情模式 -->
    <template v-else>
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

      <div v-loading="detailLoading">
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
              <el-descriptions-item label="用户">
                <span>{{ selectedIdentityName }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="身份来源">
                <div v-if="selectedIdentity?.accounts.length" class="account-list">
                  <div
                    v-for="account in selectedIdentity.accounts"
                    :key="account.actor_id"
                    class="account-item"
                  >
                    <el-tag size="small" type="info">{{ account.frontend }}</el-tag>
                    <span v-if="account.display_name">{{ account.display_name }}</span>
                    <span class="mono">{{ account.external_key }}</span>
                  </div>
                </div>
                <span v-else class="muted">未关联身份数据</span>
              </el-descriptions-item>
              <el-descriptions-item label="内部用户 ID">
                <span class="mono muted">{{ rel.user_id }}</span>
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
          v-else-if="!detailLoading && !errMsg"
          description="尚无该用户的关系记录"
        />
      </div>
    </template>

    <RelationshipEditDialog
      v-model="editDialogVisible"
      :submitting="editSaving"
      :relationship="rel"
      @submit="submitEdit"
    />
  </div>
</template>

<style lang="scss" scoped>
.account-list {
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.account-item {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: $space-2;
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