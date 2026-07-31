<script setup lang="ts">
/**
 * 插件管理页面 (v0.3.3).
 *
 * 列出所有已发现的身份解析插件，显示名称和描述。
 */
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listPlugins } from '@/api/client'
import type { PluginInfo } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'

const items = ref<PluginInfo[]>([])
const loading = ref(false)

async function refresh() {
  loading.value = true
  try {
    const res = await listPlugins()
    items.value = res.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="page-container">
    <PageHeader
      title="插件管理"
      subtitle="管理已发现的身份解析插件。插件由 plugins/ 目录自动加载，无需额外配置。"
    >
      <template #actions>
        <el-button :loading="loading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </template>
    </PageHeader>

    <el-card>
      <el-table
        :data="items"
        :loading="loading"
        stripe
        style="width: 100%"
      >
        <el-table-column prop="name" label="插件名称" min-width="160">
          <template #default="{ row }: { row: PluginInfo }">
            <el-tag>{{ row.name }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="300">
          <template #default="{ row }: { row: PluginInfo }">
            <span class="muted">{{ row.description || '暂无描述' }}</span>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="!loading && items.length === 0" class="empty-state">
        <el-empty description="暂未发现插件" />
      </div>
    </el-card>
  </div>
</template>

<style lang="scss" scoped>
.muted {
  color: var(--el-text-color-secondary);
}

.empty-state {
  padding: $space-8 0;
}
</style>
