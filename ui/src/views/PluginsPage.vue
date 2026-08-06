<script setup lang="ts">
/**
 * 插件管理页面 (v0.3.1).
 *
 * - 从远程源浏览可用插件，一键安装
 * - 管理本地已安装插件，可删除
 */
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getPluginProxy,
  installPlugin,
  listAvailablePlugins,
  listInstalledPlugins,
  removePlugin,
  setPluginProxy,
} from '@/api/identity'
import type { AvailablePluginInfo, InstalledPluginInfo } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'

// 已安装
const installed = ref<InstalledPluginInfo[]>([])
const installedLoading = ref(false)

// 可用
const available = ref<AvailablePluginInfo[]>([])
const availableLoading = ref(false)

// 代理配置
const proxy = ref('')
const proxySaving = ref(false)
const proxyLoaded = ref(false)

// 安装/删除中
const installing = ref<Set<string>>(new Set())
const removing = ref<Set<string>>(new Set())

async function loadProxy() {
  try {
    const res = await getPluginProxy()
    proxy.value = res.plugin_proxy || ''
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    proxyLoaded.value = true
  }
}

async function saveProxy() {
  proxySaving.value = true
  try {
    const res = await setPluginProxy(proxy.value.trim())
    proxy.value = res.plugin_proxy || ''
    ElMessage.success('代理设置已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    proxySaving.value = false
  }
}

async function refresh() {
  await Promise.all([refreshInstalled(), refreshAvailable()])
}

async function refreshInstalled() {
  installedLoading.value = true
  try {
    const res = await listInstalledPlugins()
    installed.value = res.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    installedLoading.value = false
  }
}

async function refreshAvailable() {
  availableLoading.value = true
  try {
    const res = await listAvailablePlugins()
    available.value = res.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    availableLoading.value = false
  }
}

async function handleInstall(plugin: AvailablePluginInfo) {
  const name = plugin.name || plugin.file_name
  try {
    await ElMessageBox.confirm(
      `安装插件「${name}」？文件将下载到 plugins/ 目录，重启后生效。`,
      '安装插件',
      { confirmButtonText: '安装', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  installing.value.add(plugin.file_name)
  try {
    await installPlugin(plugin.file_name, plugin.download_url)
    ElMessage.success(`插件「${name}」已安装，重启后生效`)
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    installing.value.delete(plugin.file_name)
  }
}

async function handleRemove(file_name: string, display_name: string) {
  try {
    await ElMessageBox.confirm(
      `删除插件「${display_name}」(${file_name})？删除后需要重启才能生效。`,
      '删除插件',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }

  removing.value.add(file_name)
  try {
    await removePlugin(file_name)
    ElMessage.success('插件已删除，重启后生效')
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    removing.value.delete(file_name)
  }
}

onMounted(() => {
  refresh()
  loadProxy()
})
</script>

<template>
  <div class="page-container">
    <PageHeader
      title="插件管理"
      subtitle="浏览远程插件源并安装，或管理本地已安装的插件。插件放入 plugins/ 目录后重启即生效。"
    >
      <template #actions>
        <el-button :loading="installedLoading || availableLoading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </template>
    </PageHeader>

    <!-- 代理设置 -->
    <el-card class="section-card">
      <template #header>
        <div class="section-header">
          <span class="section-title">代理设置</span>
          <span class="section-subtitle">GitHub 无法直接访问时, 配置前缀代理 (如 gh-proxy.org) 用于检索与下载插件</span>
        </div>
      </template>
      <div class="proxy-row">
        <el-input
          v-model="proxy"
          placeholder="https://gh-proxy.org/... 留空不使用"
          clearable
          class="proxy-input"
          :disabled="!proxyLoaded"
        />
        <el-button type="primary" :loading="proxySaving" @click="saveProxy">
          保存
        </el-button>
      </div>
    </el-card>

    <!-- 可用插件 -->
    <el-card class="section-card">
      <template #header>
        <div class="section-header">
          <span class="section-title">可用插件</span>
          <span class="section-subtitle">从远程插件源浏览，点击安装</span>
        </div>
      </template>

      <el-table
        :data="available"
        :loading="availableLoading"
        stripe
        style="width: 100%"
        empty-text="暂无可用插件（插件源为空或无法访问）"
      >
        <el-table-column label="插件" min-width="200">
          <template #default="{ row }: { row: AvailablePluginInfo }">
            <div class="plugin-name">
              <strong>{{ row.name || row.file_name }}</strong>
              <el-tag v-if="row.installed" type="success" size="small" class="installed-tag">
                已安装
              </el-tag>
            </div>
            <div v-if="row.version" class="plugin-meta">v{{ row.version }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="300">
          <template #default="{ row }: { row: AvailablePluginInfo }">
            <span class="muted">{{ row.description || '暂无描述' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="作者" width="120">
          <template #default="{ row }: { row: AvailablePluginInfo }">
            <span class="muted">{{ row.author || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="right" fixed="right">
          <template #default="{ row }: { row: AvailablePluginInfo }">
            <el-button
              v-if="!row.installed"
              type="primary"
              size="small"
              :loading="installing.has(row.file_name)"
              @click="handleInstall(row)"
            >
              安装
            </el-button>
            <span v-else class="muted">已安装</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 已安装插件 -->
    <el-card class="section-card">
      <template #header>
        <div class="section-header">
          <span class="section-title">已安装插件</span>
          <span class="section-subtitle">本地 plugins/ 目录中的插件</span>
        </div>
      </template>

      <el-table
        :data="installed"
        :loading="installedLoading"
        stripe
        style="width: 100%"
        empty-text="暂未安装插件"
      >
        <el-table-column label="插件" min-width="200">
          <template #default="{ row }: { row: InstalledPluginInfo }">
            <el-tag>{{ row.name || row.file_name }}</el-tag>
            <div v-if="row.version" class="plugin-meta">v{{ row.version }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="file_name" label="文件" min-width="160">
          <template #default="{ row }: { row: InstalledPluginInfo }">
            <code class="file-name">{{ row.file_name }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="300">
          <template #default="{ row }: { row: InstalledPluginInfo }">
            <span class="muted">{{ row.description || '暂无描述' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="right" fixed="right">
          <template #default="{ row }: { row: InstalledPluginInfo }">
            <el-button
              link
              type="danger"
              :loading="removing.has(row.file_name)"
              @click="handleRemove(row.file_name, row.name || row.file_name)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style lang="scss" scoped>
.section-card {
  margin-bottom: $space-4;
}

.section-header {
  display: flex;
  align-items: baseline;
  gap: $space-2;
}

.section-title {
  font-weight: 600;
  font-size: 15px;
}

.section-subtitle {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.proxy-row {
  display: flex;
  gap: $space-2;
  align-items: center;
}

.proxy-input {
  max-width: 480px;
}

.plugin-name {
  display: flex;
  align-items: center;
  gap: $space-2;
}

.installed-tag {
  flex-shrink: 0;
}

.plugin-meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}

.file-name {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.muted {
  color: var(--el-text-color-secondary);
}
</style>
