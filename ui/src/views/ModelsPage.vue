<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  listModelBindings,
  addModelBinding,
  deleteModelBinding,
  reorderModelBindings,
  listUpstreamServices,
  listUpstreamAvailableModels,
} from '@/api/client'
import type {
  RoleBindingItem,
  UpstreamModelType,
  UpstreamService,
} from '@/types/api'

interface RoleMeta {
  key: UpstreamModelType
  title: string
  desc: string
}

const ROLES: RoleMeta[] = [
  { key: 'main', title: '主模型', desc: '主对话与工具调用, 影响用户体验最直接' },
  { key: 'assist', title: '辅助模型', desc: '记忆/关系分析等后台任务, 通常选便宜些的' },
  { key: 'embedding', title: '嵌入模型', desc: '记忆向量化; 维度由所选模型决定' },
  { key: 'rerank', title: '重排序模型', desc: '召回后的相关性重排; 可选' },
]

const bindings = ref<Record<UpstreamModelType, RoleBindingItem[]>>({
  main: [],
  assist: [],
  embedding: [],
  rerank: [],
})
const services = ref<UpstreamService[]>([])
const loading = ref(false)

// ── Add dialog ─────────────────────────────────────────────────────────────
const addDialog = ref(false)
const addRef = ref<FormInstance | null>(null)
const addForm = reactive({
  role: 'main' as UpstreamModelType,
  service_id: '',
  model: '',
  priority: null as number | null,
})
const addSubmitting = ref(false)
const availableModels = ref<string[]>([])
const availableLoading = ref(false)

const addRules: FormRules = {
  service_id: [{ required: true, message: '请选择服务商', trigger: 'change' }],
  model: [{ required: true, message: '请填写模型名', trigger: 'blur' }],
}

const currentRolePriorityCap = computed(() => bindings.value[addForm.role]?.length ?? 0)

// ──────────────────────────────────────────────────────────────────────────
async function refresh() {
  loading.value = true
  try {
    const [all, svcs] = await Promise.all([
      listModelBindings(),
      listUpstreamServices(),
    ])
    const grouped: Record<UpstreamModelType, RoleBindingItem[]> = {
      main: [], assist: [], embedding: [], rerank: [],
    }
    for (const item of all.items) {
      grouped[item.role].push(item)
    }
    for (const r of Object.keys(grouped) as UpstreamModelType[]) {
      grouped[r].sort((a, b) => a.priority - b.priority)
    }
    bindings.value = grouped
    services.value = svcs
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function openAdd(role: UpstreamModelType) {
  addForm.role = role
  addForm.service_id = services.value[0]?.id ?? ''
  addForm.model = ''
  addForm.priority = null
  availableModels.value = []
  addDialog.value = true
  if (addForm.service_id) fetchAvailable(addForm.service_id)
}

async function fetchAvailable(id: string) {
  availableLoading.value = true
  try {
    const res = await listUpstreamAvailableModels(id)
    availableModels.value = res.models
  } catch (err) {
    availableModels.value = []
    ElMessage.warning(
      '拉取模型列表失败, 可手动输入: ' +
        (err instanceof Error ? err.message : String(err)),
    )
  } finally {
    availableLoading.value = false
  }
}

function onAddServiceChange(id: string) {
  addForm.model = ''
  availableModels.value = []
  if (id) fetchAvailable(id)
}

async function onAdd() {
  if (!addRef.value) return
  const ok = await addRef.value.validate().catch(() => false)
  if (!ok) return
  addSubmitting.value = true
  try {
    await addModelBinding({
      role: addForm.role,
      service_id: addForm.service_id,
      model: addForm.model.trim(),
      priority: addForm.priority,
    })
    ElMessage.success('已添加')
    addDialog.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    addSubmitting.value = false
  }
}

async function onDelete(item: RoleBindingItem) {
  try {
    await ElMessageBox.confirm(
      `将从『${roleTitle(item.role)}』移除候选: ${item.service_id} / ${item.model}`,
      '删除候选',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteModelBinding(item.role, item.priority)
    ElMessage.success('已删除')
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function move(role: UpstreamModelType, index: number, delta: -1 | 1) {
  const list = [...bindings.value[role]]
  const target = index + delta
  if (target < 0 || target >= list.length) return
  const a = list[index]
  const b = list[target]
  if (!a || !b) return
  list[index] = b
  list[target] = a
  const order: [string, string][] = list.map((i) => [i.service_id, i.model])
  try {
    await reorderModelBindings(role, order)
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function roleTitle(k: UpstreamModelType): string {
  return ROLES.find((r) => r.key === k)?.title ?? k
}
</script>

<template>
  <div class="page-container">
    <div class="page-head">
      <div>
        <h2 class="page-title">模型管理</h2>
        <p class="page-subtitle">
          按角色维护候选优先级列表: priority 0 为首选, 上游返回可重试错误 (5xx / 超时) 时自动回退到下一位。
          编辑后立即生效, 正在进行的流式请求继续使用旧配置。
        </p>
      </div>
      <div class="head-actions">
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="!loading && services.length === 0"
      type="warning"
      show-icon
      :closable="false"
      style="margin-bottom: 16px"
    >
      尚未配置任何上游服务, 请先前往
      <router-link to="/upstream" class="inline-link">『上游 API』</router-link>
      添加服务商。
    </el-alert>

    <div v-loading="loading" class="grid">
      <el-card
        v-for="meta in ROLES"
        :key="meta.key"
        shadow="hover"
        class="role-card"
      >
        <template #header>
          <div class="role-head">
            <div class="role-title">
              <span>{{ meta.title }}</span>
              <span class="role-count">{{ bindings[meta.key].length }} 个候选</span>
            </div>
            <el-button
              type="primary"
              size="small"
              :disabled="services.length === 0"
              @click="openAdd(meta.key)"
            >
              <el-icon><Plus /></el-icon>
              <span>添加候选</span>
            </el-button>
          </div>
          <div class="role-desc">{{ meta.desc }}</div>
        </template>

        <div v-if="bindings[meta.key].length === 0" class="empty">
          <el-empty :image-size="60" description="尚无候选" />
        </div>
        <ol v-else class="cand-list">
          <li
            v-for="(item, idx) in bindings[meta.key]"
            :key="`${item.service_id}::${item.model}`"
            class="cand-row"
          >
            <span class="cand-prio" :class="{ top: item.priority === 0 }">
              #{{ item.priority }}
            </span>
            <div class="cand-body">
              <div class="cand-model mono">{{ item.model }}</div>
              <div class="cand-svc muted mono">via {{ item.service_id }}</div>
            </div>
            <div class="cand-actions">
              <el-button
                link
                size="small"
                :disabled="idx === 0"
                @click="move(meta.key, idx, -1)"
              >
                <el-icon><Top /></el-icon>
              </el-button>
              <el-button
                link
                size="small"
                :disabled="idx === bindings[meta.key].length - 1"
                @click="move(meta.key, idx, 1)"
              >
                <el-icon><Bottom /></el-icon>
              </el-button>
              <el-button link type="danger" size="small" @click="onDelete(item)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
          </li>
        </ol>
      </el-card>
    </div>

    <!-- Add dialog -->
    <el-dialog
      v-model="addDialog"
      :title="`添加候选: ${roleTitle(addForm.role)}`"
      width="520px"
    >
      <el-form
        ref="addRef"
        :model="addForm"
        :rules="addRules"
        label-width="100px"
      >
        <el-form-item label="服务商" prop="service_id">
          <el-select
            v-model="addForm.service_id"
            placeholder="选择上游服务"
            style="width: 100%"
            @change="onAddServiceChange"
          >
            <el-option
              v-for="s in services"
              :key="s.id"
              :label="s.id"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="模型名" prop="model">
          <el-select
            v-model="addForm.model"
            filterable
            allow-create
            default-first-option
            placeholder="从可用列表选择或直接输入"
            style="width: 100%"
            :loading="availableLoading"
          >
            <el-option
              v-for="m in availableModels"
              :key="m"
              :label="m"
              :value="m"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-input-number
            v-model="addForm.priority"
            :min="0"
            :max="currentRolePriorityCap"
            :placeholder="`留空则排到末尾 (当前末尾: ${currentRolePriorityCap})`"
            style="width: 100%"
            controls-position="right"
          />
          <div class="hint">
            0 为最高优先级; 留空则追加到末尾。指定已被占用的位置时, 现有候选会向后顺移。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialog = false">取消</el-button>
        <el-button type="primary" :loading="addSubmitting" @click="onAdd">
          添加
        </el-button>
      </template>
    </el-dialog>
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
}

.inline-link {
  color: var(--el-color-primary);
  text-decoration: none;

  &:hover {
    text-decoration: underline;
  }
}

.grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: $space-4;

  @include respond-to(lg) {
    grid-template-columns: repeat(2, 1fr);
  }
}

.role-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.role-title {
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: $space-2;
}

.role-count {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
  padding: 2px 8px;
  border-radius: $radius-sm;
  background: var(--el-fill-color);
}

.role-desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: $space-1;
}

.empty {
  padding: $space-2 0;
}

.cand-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.cand-row {
  display: grid;
  grid-template-columns: 48px 1fr auto;
  align-items: center;
  gap: $space-3;
  padding: $space-2 $space-3;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: $radius-sm;
  background: var(--el-fill-color-lighter);
}

.cand-prio {
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  text-align: center;

  &.top {
    color: var(--el-color-primary);
  }
}

.cand-body {
  min-width: 0;
}

.cand-model {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cand-svc {
  font-size: 12px;
  margin-top: 2px;
}

.cand-actions {
  display: flex;
  gap: $space-1;
}

.muted {
  color: var(--el-text-color-secondary);
}

.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: $space-1;
  line-height: 1.4;
}
</style>
