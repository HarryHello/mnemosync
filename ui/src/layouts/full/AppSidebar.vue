<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { healthCheck } from '@/api/client'
import { useDarkMode } from '@/composables/useDarkMode'
import { useAuthStore } from '@/stores/auth'
import { useNotificationsStore } from '@/stores/notifications'
import NotificationDrawer from '@/components/notifications/NotificationDrawer.vue'

interface MenuItem {
  path: string
  title: string
  icon: string
}

const route = useRoute()
const router = useRouter()
const { isDark, toggle: toggleDark } = useDarkMode()
const authStore = useAuthStore()
const notificationsStore = useNotificationsStore()

const version = ref<string | null>(null)
const notificationsOpen = ref(false)

onMounted(async () => {
  if (!authStore.user) void authStore.fetchUser().catch(() => undefined)
  notificationsStore.startPolling()
  try {
    const h = await healthCheck()
    version.value = h.version
  } catch {
    version.value = null
  }
})

onUnmounted(() => {
  notificationsStore.stopPolling()
})

const items: MenuItem[] = [
  { path: '/dashboard', title: '仪表盘', icon: 'Odometer' },
  { path: '/prompts', title: '提示词', icon: 'EditPen' },
  { path: '/models', title: '模型配置', icon: 'Link' },
  { path: '/api-keys', title: 'API Keys', icon: 'Key' },
  { path: '/identity', title: '关系状态', icon: 'Connection' },
  { path: '/logs', title: '请求日志', icon: 'Document' },
  { path: '/memories', title: '记忆管理', icon: 'Cpu' },
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
  else if (cmd === 'notifications') notificationsOpen.value = true
}
</script>

<template>
  <aside class="app-sidebar">
    <div class="brand">
      <img class="brand-mark" src="/favicon.svg" alt="Mnemosync" />
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
          <el-badge
            :value="notificationsStore.unreadCount"
            :hidden="notificationsStore.unreadCount === 0"
            :max="99"
            class="avatar-badge"
          >
            <el-avatar :size="28" class="avatar">{{ username.slice(0, 1).toUpperCase() }}</el-avatar>
          </el-badge>
          <span class="user-name">{{ username }}</span>
          <el-icon><ArrowDown /></el-icon>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="notifications">
              <el-icon><Bell /></el-icon>
              <span>显示通知</span>
              <el-badge
                v-if="notificationsStore.unreadCount > 0"
                :value="notificationsStore.unreadCount"
                :max="99"
                class="dropdown-badge"
              />
            </el-dropdown-item>
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

    <NotificationDrawer v-model="notificationsOpen" />
  </aside>
</template>

<style lang="scss" scoped>
.app-sidebar {
  width: $sidebar-width;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--el-menu-bg-color);
  border-right: 1px solid var(--el-border-color);
}

.brand {
  height: $header-height;
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: 0 $space-4;
  border-bottom: 1px solid var(--el-border-color);
  font-weight: 700;
  font-size: 15px;
}

.brand-mark {
  width: 30px;
  height: 30px;
  display: block;
  object-fit: contain;
  background: transparent;
}

.menu {
  flex: 1;
  border-right: 0;
  padding: $space-2 0;
}

.menu :deep(.el-menu-item) {
  height: 40px;
  line-height: 40px;
  color: var(--el-menu-text-color);
}

.menu :deep(.el-menu-item .el-icon) {
  color: inherit;
}

.menu :deep(.el-menu-item:hover) {
  color: var(--el-text-color-primary);
}

.menu :deep(.el-menu-item.is-active) {
  color: var(--el-menu-active-color);
  background: rgba(66, 133, 244, 0.14);
}

:root.dark .menu :deep(.el-menu-item.is-active) {
  background: rgba(0, 101, 253, 0.24);
}

.footer {
  padding: $space-2 $space-3 $space-3;
  border-top: 1px solid var(--el-border-color);
  display: flex;
  flex-direction: column;
  gap: $space-1;
}

.user-trigger {
  display: inline-flex;
  align-items: center;
  gap: $space-2;
  cursor: pointer;
  padding: $space-2 $space-2;
  border-radius: $radius-md;
  min-width: 0;
  border: 1px solid transparent;

  &:hover {
    background: rgba(66, 133, 244, 0.08);
    border-color: rgba(66, 133, 244, 0.12);
  }
}

.avatar {
  background: $brand-primary;
  color: #fff;
  font-weight: 600;
  flex: 0 0 auto;
}

.avatar-badge {
  flex: 0 0 auto;
  line-height: 0;
}

.avatar-badge :deep(.el-badge__content) {
  font-size: 10px;
  height: 16px;
  line-height: 16px;
  padding: 0 4px;
  min-width: 16px;
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
  background: rgba(66, 133, 244, 0.08);
  border-radius: 999px;
  padding: 2px 6px;
}
</style>

<style lang="scss">
.sidebar-user-popper {
  border-radius: $radius-lg !important;
  border: 1px solid var(--el-border-color) !important;
  box-shadow: var(--el-box-shadow) !important;
}

.sidebar-user-popper .el-dropdown-menu__item {
  gap: 8px;
}

.sidebar-user-popper .el-dropdown-menu__item:last-child {
  background: transparent;

  &:hover,
  &:focus {
    color: var(--el-color-danger) !important;
    background: var(--el-color-danger-light-9);
  }
}

.sidebar-user-popper .dropdown-badge {
  margin-left: auto;
  padding-left: 8px;
}

.sidebar-user-popper .dropdown-badge .el-badge__content {
  position: static;
  transform: none;
}
</style>
