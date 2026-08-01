<script setup lang="ts">
/**
 * 用户组 (UserGroup) 管理 (v0.3.0).
 *
 * 一个用户组 = 一个真实人。把同一人在不同平台的 Actor 绑到同一组后,
 * 记忆与关系以组 ID (effective_user_id) 为隔离边界共享。
 */
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance } from 'element-plus'
import { formatDate } from '@/utils/format'
import {
  bindActorToGroup,
  createUserGroup,
  listActors,
  listGroupMembers,
  listUserGroups,
  unbindActorFromGroup,
} from '@/api/client'
import type { Actor, UserGroup } from '@/types/api'

const items = ref<UserGroup[]>([])
const loading = ref(false)

/** group_id → 成员列表 */
const membersByGroup = ref<Record<string, Actor[]>>({})

// 创建组对话框
const createDialog = ref(false)
const createSubmitting = ref(false)
const createFormRef = ref<FormInstance | null>(null)
const createForm = reactive({ name: '' })

// 成员管理对话框
const membersDialog = ref(false)
const membersTarget = ref<UserGroup | null>(null)
const members = ref<Actor[]>([])
const membersLoading = ref(false)

// 添加成员
const addMemberId = ref('')
const allActors = ref<Actor[]>([])
const addSubmitting = ref(false)

async function refresh() {
  loading.value = true
  try {
    const res = await listUserGroups()
    items.value = res.items
    await loadMembers(res.items)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

async function loadMembers(groups: UserGroup[]) {
  const map: Record<string, Actor[]> = {}
  await Promise.all(
    groups.map(async (g) => {
      try {
        const res = await listGroupMembers(g.id)
        map[g.id] = res.items
      } catch {
        map[g.id] = []
      }
    }),
  )
  membersByGroup.value = map
}

function membersOf(group: UserGroup): Actor[] {
  return membersByGroup.value[group.id] ?? []
}

async function openCreate() {
  createForm.name = ''
  createDialog.value = true
  await nextTick()
  createFormRef.value?.clearValidate()
}

async function submitCreate() {
  if (createSubmitting.value) return
  createSubmitting.value = true
  try {
    const created = await createUserGroup({ name: createForm.name.trim() || null })
    ElMessage.success(`用户组已创建 (${created.id.slice(0, 12)}…)`)
    createDialog.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    createSubmitting.value = false
  }
}

async function openMembers(group: UserGroup) {
  membersTarget.value = group
  addMemberId.value = ''
  membersDialog.value = true
  membersLoading.value = true
  try {
    const [membersRes, actorsRes] = await Promise.all([
      listGroupMembers(group.id),
      listActors(),
    ])
    members.value = membersRes.items
    allActors.value = actorsRes.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    membersLoading.value = false
  }
}

/** 可添加的参与者: 排除已是本组成员的 */
const addableActors = computed(() => {
  const memberIds = new Set(members.value.map((a) => a.id))
  return allActors.value.filter((a) => !memberIds.has(a.id))
})

async function addMember() {
  if (!membersTarget.value || !addMemberId.value || addSubmitting.value) return
  addSubmitting.value = true
  try {
    await bindActorToGroup(addMemberId.value, membersTarget.value.id)
    ElMessage.success('已添加成员')
    addMemberId.value = ''
    const res = await listGroupMembers(membersTarget.value.id)
    members.value = res.items
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    addSubmitting.value = false
  }
}

async function removeMember(actor: Actor) {
  if (!membersTarget.value) return
  try {
    await ElMessageBox.confirm(
      `将 "${actor.display_name || actor.external_key}" 移出用户组后, 该身份将回到独立身份 (记忆与关系不再与组共享, 已有数据不迁移)。确认移出?`,
      '移出成员',
      { type: 'warning', confirmButtonText: '移出', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await unbindActorFromGroup(actor.id, membersTarget.value.id)
    ElMessage.success('已移出')
    if (membersTarget.value) {
      const res = await listGroupMembers(membersTarget.value.id)
      members.value = res.items
    }
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

defineExpose({ refresh })
onMounted(refresh)
</script>

<template>
  <div>
    <div class="tab-toolbar">
      <p class="tab-hint">
        用户组代表一个真实人: 把同一人在不同平台的参与者加入同一组后,
        它们共享记忆与关系 (以组 ID 为 effective_user_id)。未分组的参与者各自独立。
      </p>
      <el-button type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>
        <span>创建用户组</span>
      </el-button>
    </div>

    <el-table
      v-loading="loading"
      :data="items"
      stripe
      row-key="id"
      empty-text="暂无用户组"
    >
      <el-table-column label="名称" min-width="160">
        <template #default="{ row }">
          {{ row.name || '(未命名)' }}
        </template>
      </el-table-column>
      <el-table-column label="组 ID" min-width="200">
        <template #default="{ row }">
          <span class="mono muted">{{ row.id }}</span>
        </template>
      </el-table-column>
      <el-table-column label="成员数" width="100" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="membersOf(row).length ? '' : 'info'">
            {{ membersOf(row).length }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">
          <span class="mono muted">{{ formatDate(row.created_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" align="right" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openMembers(row)">成员管理</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createDialog" title="创建用户组" width="440px">
      <el-form ref="createFormRef" :model="createForm" label-width="70px">
        <el-form-item label="名称">
          <el-input
            v-model="createForm.name"
            placeholder="例如: 张三 (可选, 便于辨认)"
            maxlength="64"
            show-word-limit
            @keyup.enter="submitCreate"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="createSubmitting" @click="createDialog = false">取消</el-button>
        <el-button type="primary" :loading="createSubmitting" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="membersDialog"
      :title="`成员管理 — ${membersTarget?.name || membersTarget?.id.slice(0, 12) || ''}`"
      width="640px"
    >
      <div class="add-member-row">
        <el-select
          v-model="addMemberId"
          placeholder="选择要添加的参与者"
          filterable
          style="flex: 1"
          :disabled="addableActors.length === 0"
        >
          <el-option
            v-for="a in addableActors"
            :key="a.id"
            :label="`${a.frontend} / ${a.external_key}${a.display_name ? ` (${a.display_name})` : ''}`"
            :value="a.id"
          />
        </el-select>
        <el-button type="primary" :disabled="!addMemberId" :loading="addSubmitting" @click="addMember">
          添加
        </el-button>
      </div>

      <el-table
        v-loading="membersLoading"
        :data="members"
        stripe
        row-key="id"
        empty-text="暂无成员 — 从上方选择参与者加入"
        max-height="360px"
      >
        <el-table-column label="前端" width="110">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.frontend }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="external_key" label="平台标识" min-width="130">
          <template #default="{ row }">
            <span class="mono">{{ row.external_key }}</span>
          </template>
        </el-table-column>
        <el-table-column label="昵称" min-width="110">
          <template #default="{ row }">
            {{ row.display_name || '—' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="right">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeMember(row)">移出</el-button>
          </template>
        </el-table-column>
      </el-table>
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

.add-member-row {
  display: flex;
  gap: $space-2;
  margin-bottom: $space-3;
}
</style>
