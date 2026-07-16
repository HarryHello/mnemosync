<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useDarkMode } from '@/composables/useDarkMode'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const { isDark, toggle: toggleDark } = useDarkMode()
const authStore = useAuthStore()

const routeTitleMap: Record<string, string> = {
  '/dashboard': '仪表盘',
  '/prompts': '提示词管理',
  '/api-keys': 'API Key',
  '/logs': '请求日志',
  '/memories': '记忆管理',
  '/relationships': '关系状态',
  '/settings': '设置',
}

const breadcrumb = computed(() => {
  const path = route.path
  // /prompts/:name 显示为 "提示词管理 / :name"
  if (path.startsWith('/prompts/') && path !== '/prompts') {
    return ['提示词管理', decodeURIComponent(path.slice('/prompts/'.length))]
  }
  const key = Object.keys(routeTitleMap)
    .filter((k) => path === k || path.startsWith(k + '/'))
    .sort((a, b) => b.length - a.length)[0]
  return key ? [routeTitleMap[key]!] : [path]
})

const username = computed(() => authStore.user?.username ?? '未登录')

async function handleLogout() {
  try {
    await ElMessageBox.confirm('确认要退出登录吗？', '提示', {
      type: 'warning',
      confirmButtonText: '退出',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await authStore.logout()
    ElMessage.success('已退出')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
  router.push('/login')
}

function handleUserCommand(cmd: string) {
  if (cmd === 'logout') void handleLogout()
  else if (cmd === 'change-password') router.push('/settings')
}

onMounted(() => {
  if (!authStore.user) void authStore.fetchUser().catch(() => undefined)
})
</script>

<template>
  <header class="app-header">
    <div class="left">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item v-for="(item, idx) in breadcrumb" :key="idx">
          {{ item }}
        </el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <div class="right">
      <el-tooltip :content="isDark ? '切换到亮色' : '切换到暗色'" placement="bottom">
        <el-button link circle @click="toggleDark">
          <el-icon :size="18">
            <component :is="isDark ? 'Sunny' : 'Moon'" />
          </el-icon>
        </el-button>
      </el-tooltip>

      <el-dropdown trigger="click" @command="handleUserCommand">
        <span class="user-trigger">
          <el-avatar :size="28" class="avatar">{{ username.slice(0, 1).toUpperCase() }}</el-avatar>
          <span class="user-name">{{ username }}</span>
          <el-icon><ArrowDown /></el-icon>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="change-password">修改密码</el-dropdown-item>
            <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<style lang="scss" scoped>
.app-header {
  height: $header-height;
  padding: 0 $space-5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-lighter);
  position: sticky;
  top: 0;
  z-index: $z-header;
}

.right {
  display: flex;
  align-items: center;
  gap: $space-3;
}

.user-trigger {
  display: inline-flex;
  align-items: center;
  gap: $space-2;
  cursor: pointer;
  padding: $space-1 $space-2;
  border-radius: $radius-sm;

  &:hover {
    background: var(--el-fill-color-light);
  }
}

.avatar {
  background: linear-gradient(135deg, $brand-primary, $brand-primary-hover);
  color: #fff;
  font-weight: 600;
}

.user-name {
  font-size: 13px;
  color: var(--el-text-color-primary);
}
</style>
