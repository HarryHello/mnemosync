<script setup lang="ts">
/**
 * 参与者 (Actor) 管理 (v0.3.0).
 *
 * Actor 由系统在处理请求时按身份策略自动创建, 面板只读 + 组绑定。
 * 一个 Actor = 一个前台应用上的一个账号 (frontend + external_key)。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { bindActorToGroup, listActorGroups, listActors, listUserGroups } from '@/api/client'
import type { Actor, UserGroup } from '@/types/api'

const items = ref<Actor[]>([])
const loading = ref(false)
const search = ref('')

/** actor_id → 所属组列表 */
const groupsByActor = ref<Record<string, UserGroup[]>>({})

// 加入组对话框
const bindDialog = ref(false)
const bindTarget = ref<Actor | null>(null)
const bindGroupId = ref('')
const bindSubmitting = ref(false)
const allGroups = ref<UserGroup[]>([])

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return items.value
  return items.value.filter(
    (a) =>
      a.external_key.toLowerCase().includes(q) ||
      a.frontend.toLowerCase().includes(q) ||
      (a.display_name ?? '').toLowerCase().includes(q) ||
      a.id.toLowerCase().includes(q),
  )
})

async function refresh() {
  loading.value = true
  try {
    const res = await listActors()
    items.value = res.items
    await loadMemberships(res.items)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

/** 并行加载每个 Actor 的组归属 (参与者数量通常有限; 超大批量时只加载前 100). */
async function loadMemberships(actors: Actor[]) {
  const map: Record<string, UserGroup[]> = {}
  const batch = actors.slice(0, 100)
  await Promise.all(
    batch.map(async (a) => {
      try {
        const res = await listActorGroups(a.id)
        map[a.id] = res.items
      } catch {
        map[a.id] = []
      }
    }),
  )
  groupsByActor.value = map
}

function groupsOf(actor: Actor): UserGroup[] {
  return groupsByActor.value[actor.id] ?? []
}

async function openBind(actor: Actor) {
  bindTarget.value = actor
  bindGroupId.value = ''
  bindDialog.value = true
  try {
    const res = await listUserGroups()
    allGroups.value = res.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

/** 对话框里可选的组: 排除该 Actor 已加入的 */
const bindableGroups = computed(() => {
  if (!bindTarget.value) return allGroups.value
  const joined = new Set(groupsOf(bindTarget.value).map((g) => g.id))
  return allGroups.value.filter((g) => !joined.has(g.id))
})

async function submitBind() {
  if (!bindTarget.value || !bindGroupId.value || bindSubmitting.value) return
  bindSubmitting.value = true
  try {
    await bindActorToGroup(bindTarget.value.id, bindGroupId.value)
    ElMessage.success('已加入用户组 — 该 Actor 的记忆与关系从此与组内其他身份共享')
    bindDialog.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    bindSubmitting.value = false
  }
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

defineExpose({ refresh })
onMounted(refresh)
</script>

<template>
  <div>
    <div class="tab-toolbar">
      <p class="tab-hint">
        参与者由系统按身份策略自动创建 (首次请求即建档)。
        将同一个人在不同平台的参与者加入同一用户组, 即可跨平台共享记忆与关系。
      </p>
      <el-input
        v-model="search"
        placeholder="搜索 前端 / 平台标识 / 昵称 / ID"
        clearable
        style="width: 280px"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
    </div>

    <el-table
      v-loading="loading"
      :data="filtered"
      stripe
      row-key="id"
      empty-text="暂无参与者 (绑定策略的请求到达后自动创建)"
    >
      <el-table-column label="前端应用" width="130">
        <template #default="{ row }">
          <el-tag size="small" type="info">{{ row.frontend }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="external_key" label="平台标识" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="mono">{{ row.external_key }}</span>
        </template>
      </el-table-column>
      <el-table-column label="昵称" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.display_name || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="所属用户组" min-width="220">
        <template #default="{ row }">
          <template v-if="groupsOf(row).length">
            <el-tag
              v-for="g in groupsOf(row)"
              :key="g.id"
              size="small"
              class="group-tag"
            >
              {{ g.name || g.id.slice(0, 12) }}
            </el-tag>
          </template>
          <span v-else class="muted">未分组 (独立身份)</span>
        </template>
      </el-table-column>
      <el-table-column label="首次出现" width="180">
        <template #default="{ row }">
          <span class="mono muted">{{ formatDate(row.created_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="110" align="right" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openBind(row)">加入组</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="bindDialog" title="加入用户组" width="480px">
      <p v-if="bindTarget" class="bind-target">
        参与者:
        <el-tag size="small" type="info">{{ bindTarget.frontend }}</el-tag>
        <span class="mono">{{ bindTarget.external_key }}</span>
        <span v-if="bindTarget.display_name">({{ bindTarget.display_name }})</span>
      </p>
      <el-form label-width="80px">
        <el-form-item label="用户组">
          <el-select
            v-model="bindGroupId"
            placeholder="选择要加入的用户组"
            style="width: 100%"
            :disabled="bindableGroups.length === 0"
          >
            <el-option
              v-for="g in bindableGroups"
              :key="g.id"
              :label="g.name ? `${g.name} (${g.id.slice(0, 12)}…)` : g.id"
              :value="g.id"
            />
          </el-select>
          <p v-if="bindableGroups.length === 0" class="form-item-hint">
            没有可加入的组 — 请先在「用户组」标签创建组, 或该参与者已加入所有组。
          </p>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="bindSubmitting" @click="bindDialog = false">取消</el-button>
        <el-button
          type="primary"
          :loading="bindSubmitting"
          :disabled="!bindGroupId"
          @click="submitBind"
        >
          加入
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style lang="scss" scoped>
.tab-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $space-4;
  margin-bottom: $space-4;
}

.tab-hint {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
  max-width: 720px;
}

.muted {
  color: var(--el-text-color-secondary);
}

.group-tag {
  margin-right: $space-1;
  margin-bottom: 2px;
}

.bind-target {
  display: flex;
  align-items: center;
  gap: $space-2;
  margin: 0 0 $space-3;
  font-size: 13px;
}

.form-item-hint {
  margin: $space-1 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
