<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { healthCheck } from '@/api/client'
import { useDarkMode } from '@/composables/useDarkMode'
import { useAuthStore } from '@/stores/auth'

interface MenuItem {
  path: string
  title: string
  icon: string
}

const route = useRoute()
const router = useRouter()
const { isDark, toggle: toggleDark } = useDarkMode()
const authStore = useAuthStore()

const version = ref<string | null>(null)

onMounted(async () => {
  if (!authStore.user) void authStore.fetchUser().catch(() => undefined)
  try {
    const h = await healthCheck()
    version.value = h.version
  } catch {
    version.value = null
  }
})

const items: MenuItem[] = [
  { path: '/dashboard', title: '仪表盘', icon: 'Odometer' },
  { path: '/prompts', title: '提示词管理', icon: 'EditPen' },
  { path: '/upstream', title: '上游 API', icon: 'Link' },
  { path: '/models', title: '模型管理', icon: 'Rank' },
  { path: '/api-keys', title: 'API Key', icon: 'Key' },
  { path: '/logs', title: '请求日志', icon: 'Document' },
  { path: '/memories', title: '记忆管理', icon: 'Cpu' },
  { path: '/maintenance', title: '记忆维护', icon: 'Tools' },
  { path: '/relationships', title: '关系状态', icon: 'Connection' },
  { path: '/debug-chat', title: '调试聊天', icon: 'ChatDotRound' },
  { path: '/settings', title: '设置', icon: 'Setting' },
]

const activePath = computed(() => {
  const match = items
    .map((i) => i.path)
    .filter((p) => route.path === p || route.path.startsWith(p + '/'))
    .sort((a, b) => b.length - a.length)[0]
  return match ?? route.path
})

const username = computed(() => authStore.user?.username ?? '未登录')

function navigate(path: string) {
  if (route.path !== path) router.push(path)
}

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
  else if (cmd === 'toggle-theme') toggleDark()
}
</script>

<template>
  <aside class="app-sidebar">
    <div class="brand">
      <span class="brand-mark">M</span>
      <span class="brand-text">Mnemosync</span>
      <span v-if="version" class="version">v{{ version }}</span>
    </div>

    <el-menu
      :default-active="activePath"
      class="menu"
      :collapse="false"
      background-color="transparent"
      @select="navigate"
    >
      <el-menu-item v-for="item in items" :key="item.path" :index="item.path">
        <el-icon>
          <component :is="item.icon" />
        </el-icon>
        <template #title>{{ item.title }}</template>
      </el-menu-item>
    </el-menu>

    <div class="footer">
      <el-dropdown trigger="hover" placement="top-start" popper-class="sidebar-user-popper" @command="handleUserCommand">
        <span class="user-trigger">
          <el-avatar :size="28" class="avatar">{{ username.slice(0, 1).toUpperCase() }}</el-avatar>
          <span class="user-name">{{ username }}</span>
          <el-icon><ArrowDown /></el-icon>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="toggle-theme">
              <el-icon>
                <component :is="isDark ? 'Sunny' : 'Moon'" />
              </el-icon>
              切换主题
            </el-dropdown-item>
            <el-dropdown-item command="change-password">
              <el-icon><EditPen /></el-icon>
              修改密码
            </el-dropdown-item>
            <el-dropdown-item command="logout">
              <el-icon color="var(--el-color-danger)"><SwitchButton /></el-icon>
              <span style="color: var(--el-color-danger);">退出登录</span>
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </aside>
</template>

<style lang="scss" scoped>
.app-sidebar {
  width: $sidebar-width;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--el-bg-color);
  border-right: 1px solid var(--el-border-color-lighter);
}

.brand {
  height: $header-height;
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: 0 $space-4;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-weight: 600;
  font-size: 15px;
}

.brand-mark {
  @include flex-center;

  width: 28px;
  height: 28px;
  border-radius: $radius-sm;
  background: linear-gradient(135deg, $brand-primary, $brand-primary-hover);
  color: #fff;
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-weight: 700;
}

.menu {
  flex: 1;
  border-right: 0;
}

.footer {
  padding: $space-2 $space-3 $space-3;
  border-top: 1px solid var(--el-border-color-lighter);
  display: flex;
  flex-direction: column;
  gap: $space-1;
}

.user-trigger {
  display: inline-flex;
  align-items: center;
  gap: $space-2;
  cursor: pointer;
  padding: $space-1 $space-2;
  border-radius: $radius-sm;
  min-width: 0;

  &:hover {
    background: var(--el-fill-color-light);
  }
}

.avatar {
  background: linear-gradient(135deg, $brand-primary, $brand-primary-hover);
  color: #fff;
  font-weight: 600;
  flex: 0 0 auto;
}

.user-name {
  font-size: 13px;
  color: var(--el-text-color-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.version {
  font-size: 10px;
  color: var(--el-text-color-secondary);
  text-align: center;
  margin-left: $space-2;
}
</style>

<style lang="scss">
.sidebar-user-popper .el-dropdown-menu__item:last-child {
  background: transparent;

  &:hover,
  &:focus {
    color: var(--el-color-danger) !important;
    background: var(--el-color-danger-light-9);
  }
}
</style>
