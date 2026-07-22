<script setup lang="ts">
import { computed } from 'vue'
import type { ApiKeyCreateResponse } from '@/types/api'

const props = defineProps<{
  modelValue: boolean
  apiKey: ApiKeyCreateResponse | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  copy: [value: string]
  closed: []
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})
</script>

<template>
  <el-dialog
    v-model="visible"
    title="Key 创建成功"
    width="520px"
    :close-on-click-modal="false"
    @closed="emit('closed')"
  >
    <el-alert type="success" :closable="false" show-icon>
      <template #title>
        Key 已生成并加密存储。你也可以稍后在列表页点击 Key 再次复制。
      </template>
    </el-alert>
    <div v-if="apiKey" class="secret-block">
      <div class="secret-label">API Key</div>
      <div class="secret-value">
        <span class="mono">{{ apiKey.key }}</span>
        <el-button size="small" @click="emit('copy', apiKey.key)">
          <el-icon><CopyDocument /></el-icon>
          <span>复制</span>
        </el-button>
      </div>
      <div class="secret-label">备注</div>
      <div class="secret-note">{{ apiKey.note }}</div>
    </div>
    <template #footer>
      <el-button type="primary" @click="visible = false">我已保存</el-button>
    </template>
  </el-dialog>
</template>

<style lang="scss" scoped>
.secret-block {
  margin-top: $space-4;
}

.secret-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-bottom: $space-1;
}

.secret-value {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: $space-2 $space-3;
  border-radius: $radius-sm;
  background: var(--el-fill-color-light);
  margin-bottom: $space-3;
  word-break: break-all;

  .mono {
    flex: 1;
    font-size: 13px;
  }
}

.secret-note {
  color: var(--el-text-color-primary);
}
</style>
