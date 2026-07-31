<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import type { IdentityStrategy } from '@/types/api'

const props = defineProps<{
  modelValue: boolean
  submitting: boolean
  strategies: IdentityStrategy[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [note: string, strategyId: string | null]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const form = reactive({ note: '', strategy_id: '' })
const formRef = ref<FormInstance | null>(null)
const rules: FormRules = {
  note: [{ required: true, message: '请填写用途备注', trigger: 'blur' }],
}

const activeStrategies = computed(() => props.strategies.filter((s) => s.is_active))

watch(
  () => props.modelValue,
  (value) => {
    if (!value) return
    form.note = ''
    form.strategy_id = ''
    void nextTick(() => formRef.value?.clearValidate())
  },
)

async function submit() {
  if (!formRef.value || props.submitting) return
  const valid = await formRef.value.validate().catch(() => false)
  if (valid) emit('submit', form.note, form.strategy_id || null)
}
</script>

<template>
  <el-dialog v-model="visible" title="创建 API Key" width="520px">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
      <el-form-item label="备注" prop="note">
        <el-input
          v-model="form.note"
          placeholder="例如: Cursor / Cherry Studio / 我的桌面客户端"
          maxlength="128"
          show-word-limit
        />
      </el-form-item>
      <el-form-item label="身份策略" prop="strategy_id">
        <el-select
          v-model="form.strategy_id"
          placeholder="不归属 (默认)"
          clearable
          style="width: 100%"
        >
          <el-option
            v-for="s in activeStrategies"
            :key="s.id"
            :label="`${s.name} (${s.strategy_type})`"
            :value="s.id"
          />
        </el-select>
        <p class="strategy-hint">
          <template v-if="!form.strategy_id">
            不归属模式: 该 Key 的请求不建立身份, 不读写任何用户的私有记忆。
          </template>
          <template v-else>
            绑定后, 该 Key 的请求按所选策略解析参与者身份。
          </template>
          策略可在<a href="/identity">「关系状态/身份管理」</a>页创建。
        </p>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="submitting" @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">创建</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.strategy-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}
</style>
