<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'

const props = defineProps<{
  modelValue: boolean
  submitting: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [note: string]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const form = reactive({ note: '' })
const formRef = ref<FormInstance | null>(null)
const rules: FormRules = {
  note: [{ required: true, message: '请填写用途备注', trigger: 'blur' }],
}

watch(
  () => props.modelValue,
  (value) => {
    if (!value) return
    form.note = ''
    void nextTick(() => formRef.value?.clearValidate())
  },
)

async function submit() {
  if (!formRef.value || props.submitting) return
  const valid = await formRef.value.validate().catch(() => false)
  if (valid) emit('submit', form.note)
}
</script>

<template>
  <el-dialog v-model="visible" title="创建 API Key" width="480px">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
      <el-form-item label="备注" prop="note">
        <el-input
          v-model="form.note"
          placeholder="例如: Cursor / Cherry Studio / 我的桌面客户端"
          maxlength="128"
          show-word-limit
          @keyup.enter="submit"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="submitting" @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">创建</el-button>
    </template>
  </el-dialog>
</template>
