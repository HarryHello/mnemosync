<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getPersonaConfig,
  resetPersonaConfig,
  updatePersonaConfig,
} from '@/api/client'
import type { PersonaConfigRead } from '@/types/api'

const props = defineProps<{
  active: boolean
}>()

const loading = ref(false)
const persona = ref<PersonaConfigRead | null>(null)
const editName = ref('')
const editPrompt = ref('')
const editAddr = ref('')
const editUserAddr = ref('')
const editContext = ref('')

function hydrate(p: PersonaConfigRead) {
  persona.value = p
  editName.value = p.name
  editPrompt.value = p.prompt
  editAddr.value = p.relation.persona_addressing
  editUserAddr.value = p.relation.user_addressing
  editContext.value = p.relation.context
}

async function load() {
  loading.value = true
  try {
    hydrate(await getPersonaConfig())
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

async function onSave() {
  if (!editName.value.trim()) {
    ElMessage.warning('人格名称不能为空')
    return
  }
  if (!editPrompt.value.trim()) {
    ElMessage.warning('人格提示词不能为空')
    return
  }
  loading.value = true
  try {
    hydrate(
      await updatePersonaConfig({
        name: editName.value.trim(),
        prompt: editPrompt.value,
        relation: {
          persona_addressing: editAddr.value.trim(),
          user_addressing: editUserAddr.value.trim(),
          context: editContext.value.trim(),
        },
      }),
    )
    ElMessage.success('人格已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

async function onReset() {
  if (!persona.value?.overridden) {
    ElMessage.info('人格未覆盖, 无需重置')
    return
  }
  try {
    await ElMessageBox.confirm(
      '确认重置人格为默认？当前覆盖将被删除, 回退到 config.local.toml / 资源默认值。',
      '重置人格',
      { type: 'warning', confirmButtonText: '重置', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  loading.value = true
  try {
    hydrate(await resetPersonaConfig())
    ElMessage.success('人格已重置为默认')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

watch(
  () => props.active,
  (active) => {
    if (active && !persona.value) load()
  },
  { immediate: true },
)
</script>

<template>
  <div>
    <div class="tab-head">
      <div>
        <h3 class="tab-title">人格编辑</h3>
        <p class="tab-subtitle">
          编辑服务器人格。修改将写入
          <span class="mono">data/persona_override.toml</span>, 运行时热重载。
        </p>
      </div>
      <div class="head-actions">
        <el-tag v-if="persona?.overridden" type="warning" size="small">已覆盖</el-tag>
        <el-tag v-else type="success" size="small">默认</el-tag>
      </div>
    </div>

    <el-card v-loading="loading">
      <el-form label-width="140px" class="persona-form">
        <el-form-item label="人格名称">
          <el-input v-model="editName" placeholder="绫音" />
        </el-form-item>

        <el-form-item label="人格提示词">
          <el-input
            v-model="editPrompt"
            type="textarea"
            :rows="12"
            placeholder="完整人格提示词..."
            class="mono"
          />
        </el-form-item>

        <el-divider content-position="left">关系框架</el-divider>

        <el-form-item label="人格自称">
          <el-input v-model="editAddr" placeholder="我" />
        </el-form-item>

        <el-form-item label="人格称呼用户">
          <el-input v-model="editUserAddr" placeholder="哥哥" />
        </el-form-item>

        <el-form-item label="关系背景">
          <el-input
            v-model="editContext"
            placeholder="同住的兄妹, 无血缘关系"
            type="textarea"
            :rows="2"
          />
        </el-form-item>

        <el-form-item>
          <div class="form-actions">
            <el-button type="primary" :loading="loading" @click="onSave">保存</el-button>
            <el-button
              type="danger"
              :disabled="!persona?.overridden"
              :loading="loading"
              @click="onReset"
            >
              重置为默认
            </el-button>
          </div>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<style lang="scss" scoped>
.tab-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $space-4;
  margin-bottom: $space-4;
  flex-wrap: wrap;
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

.persona-form {
  max-width: 800px;
}

.form-actions {
  display: flex;
  gap: $space-2;
}
</style>
