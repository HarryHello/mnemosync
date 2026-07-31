<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { Relationship } from '@/types/api'

const props = defineProps<{
  modelValue: boolean
  submitting?: boolean
  relationship: Relationship | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [payload: { persona_addressing?: string; user_addressing?: string; context?: string; reason: string }]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const form = reactive({
  persona_addressing: '',
  user_addressing: '',
  context: '',
  reason: '',
})

watch(() => props.modelValue, (v) => {
  if (v && props.relationship) {
    form.persona_addressing = props.relationship.persona_addressing
    form.user_addressing = props.relationship.user_addressing
    form.context = props.relationship.context
    form.reason = ''
  }
})

function submit() {
  const reason = form.reason.trim()
  if (reason.length < 5) {
    ElMessage.warning('原因至少 5 字')
    return
  }
  const payload: { reason: string; persona_addressing?: string; user_addressing?: string; context?: string } = { reason }
  let changed = false
  if (props.relationship) {
    if (form.persona_addressing !== props.relationship.persona_addressing) {
      payload.persona_addressing = form.persona_addressing
      changed = true
    }
    if (form.user_addressing !== props.relationship.user_addressing) {
      payload.user_addressing = form.user_addressing
      changed = true
    }
    if (form.context !== props.relationship.context) {
      payload.context = form.context
      changed = true
    }
  }
  if (!changed) {
    ElMessage.info('没有可保存的变更')
    return
  }
  emit('submit', payload)
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="编辑称呼与关系背景"
    width="520px"
    :close-on-click-modal="false"
  >
    <el-form label-position="top" size="default">
      <el-form-item label="人格自称 (persona_addressing)">
        <el-input v-model="form.persona_addressing" placeholder="例如: 我 / 人家" />
      </el-form-item>
      <el-form-item label="用户称呼 (user_addressing)">
        <el-input v-model="form.user_addressing" placeholder="例如: 你 / 小哥 / 亲爱的" />
      </el-form-item>
      <el-form-item label="关系背景 (context)">
        <el-input
          v-model="form.context"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 5 }"
          placeholder="例如: 同住兄妹 / 恋人 / 主治医生"
        />
      </el-form-item>
      <el-form-item label="修改原因 (至少 5 字, 会写入审计日志)">
        <el-input
          v-model="form.reason"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 4 }"
          placeholder="记录本次修改的背景, 便于事后回顾"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>
