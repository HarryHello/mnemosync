<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  listModelBindings,
  deleteModelBinding,
  reorderModelBindings,
  listUpstreamServices,
} from '@/api/client'
import type {
  RoleBindingItem,
  UpstreamModelType,
  UpstreamService,
} from '@/types/api'
import ModelBindingsSection from './ModelBindingsSection.vue'
import ModelBindingDialog from './ModelBindingDialog.vue'

const ROLE_TITLES: Record<UpstreamModelType, string> = {
  main: '主模型',
  assist: '辅助模型',
  embedding: '嵌入模型',
  rerank: '重排序模型',
}

const bindings = ref<Record<UpstreamModelType, RoleBindingItem[]>>({
  main: [],
  assist: [],
  embedding: [],
  rerank: [],
})
const services = ref<UpstreamService[]>([])
const loading = ref(false)
const servicesEmpty = computed(() => services.value.length === 0)

const dialogRef = ref<InstanceType<typeof ModelBindingDialog> | null>(null)

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
  dialogRef.value?.openAdd(role)
}

function onEdit(item: RoleBindingItem) {
  dialogRef.value?.openEdit(item)
}

async function openReplaceEmbedding() {
  const existing = bindings.value.embedding[0]
  if (!existing) {
    openAdd('embedding')
    return
  }
  try {
    await ElMessageBox.confirm(
      `更换嵌入模型会使全部已存记忆的向量作废, 必须运行 Reindex 重建。旧模型: ${existing.service_id}/${existing.model}`,
      '替换嵌入模型',
      {
        type: 'warning',
        confirmButtonText: '继续',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  dialogRef.value?.openReplace(existing)
}

async function onDelete(item: RoleBindingItem) {
  const isEmbedding = item.role === 'embedding'
  try {
    await ElMessageBox.confirm(
      isEmbedding
        ? `将删除嵌入绑定 ${item.service_id}/${item.model}。删除后须重新添加嵌入模型才能写入新记忆, 且已存向量将无法与新模型对齐。`
        : `将从『${ROLE_TITLES[item.role]}』移除候选: ${item.service_id} / ${item.model}`,
      isEmbedding ? '删除嵌入绑定' : '删除候选',
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

async function onReorder(role: UpstreamModelType, ordered: RoleBindingItem[]) {
  bindings.value[role] = ordered.map((item, idx) => ({ ...item, priority: idx }))
  const order: [string, string][] = ordered.map((i) => [i.service_id, i.model])
  try {
    await reorderModelBindings(role, order)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
    await refresh()
  }
}

onMounted(() => {
  refresh()
})

defineExpose({ refresh })
</script>

<template>
  <div>
    <div class="tab-head">
      <div>
        <h3 class="tab-title">模型管理</h3>
        <p class="tab-subtitle">
          按角色维护候选优先级列表: priority 0 为首选, 上游返回可重试错误 (5xx / 超时) 时自动回退到下一位。嵌入模型为单绑定, 更换需走重建流程。
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
      v-if="!loading && servicesEmpty"
      type="warning"
      show-icon
      :closable="false"
      style="margin-bottom: 16px"
    >
      尚未配置任何上游服务, 请先切换到『上游 API』tab 添加服务商。
    </el-alert>

    <div v-loading="loading">
      <ModelBindingsSection
        :bindings="bindings"
        :services-empty="servicesEmpty"
        @add="openAdd"
        @replace="openReplaceEmbedding"
        @reorder="onReorder"
        @remove="onDelete"
        @edit="onEdit"
      />
    </div>

    <ModelBindingDialog
      ref="dialogRef"
      :services="services"
      :bindings="bindings"
      @saved="refresh"
    />
  </div>
</template>

<style lang="scss" scoped>
.tab-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $space-4;
  margin-bottom: $space-4;

}

.tab-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 $space-1;
}

.tab-subtitle {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin: 0;
}

.head-actions {
  display: flex;
  gap: $space-2;
  align-items: center;
}
</style>
