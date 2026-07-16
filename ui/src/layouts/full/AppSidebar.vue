<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

interface MenuItem {
  path: string
  title: string
  icon: string
}

const route = useRoute()
const router = useRouter()

const items: MenuItem[] = [
  { path: '/dashboard', title: '仪表盘', icon: 'Odometer' },
  { path: '/prompts', title: '提示词管理', icon: 'EditPen' },
  { path: '/upstream', title: '上游 API', icon: 'Link' },
  { path: '/api-keys', title: 'API Key', icon: 'Key' },
  { path: '/logs', title: '请求日志', icon: 'Document' },
  { path: '/memories', title: '记忆管理', icon: 'Cpu' },
  { path: '/relationships', title: '关系状态', icon: 'Connection' },
  { path: '/settings', title: '设置', icon: 'Setting' },
]

const activePath = computed(() => {
  // 匹配以菜单项 path 开头的当前路由 (处理 /prompts/:name 高亮到 /prompts)
  const match = items
    .map((i) => i.path)
    .filter((p) => route.path === p || route.path.startsWith(p + '/'))
    .sort((a, b) => b.length - a.length)[0]
  return match ?? route.path
})

function navigate(path: string) {
  if (route.path !== path) router.push(path)
}
</script>

<template>
  <aside class="app-sidebar">
    <div class="brand">
      <span class="brand-mark">M</span>
      <span class="brand-text">Mnemosync</span>
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
      <span>v0.2.2</span>
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
  padding: $space-3 $space-4;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  border-top: 1px solid var(--el-border-color-lighter);
  text-align: center;
}
</style>
