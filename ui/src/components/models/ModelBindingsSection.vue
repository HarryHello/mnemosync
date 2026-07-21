<script setup lang="ts">
import RoleCardMulti from './RoleCardMulti.vue'
import RoleCardSingle from './RoleCardSingle.vue'
import type { RoleBindingItem, UpstreamModelType } from '@/types/api'

interface RoleMeta {
  key: UpstreamModelType
  title: string
  desc: string
  singleBinding: boolean
}

const ROLES: RoleMeta[] = [
  { key: 'main',      title: '主模型',     desc: '主对话与工具调用, 影响用户体验最直接',       singleBinding: false },
  { key: 'assist',    title: '辅助模型',   desc: '记忆/关系分析等后台任务, 通常选便宜些的',    singleBinding: false },
  { key: 'embedding', title: '嵌入模型',   desc: '记忆向量化; 单绑定, 更换需重建向量库',     singleBinding: true  },
  { key: 'rerank',    title: '重排序模型', desc: '召回后的相关性重排; 可选',                singleBinding: false },
]

defineProps<{
  bindings: Record<UpstreamModelType, RoleBindingItem[]>
  servicesEmpty: boolean
}>()

const emit = defineEmits<{
  add: [role: UpstreamModelType]
  replace: []
  reorder: [role: UpstreamModelType, ordered: RoleBindingItem[]]
  remove: [item: RoleBindingItem]
  edit: [item: RoleBindingItem]
}>()
</script>

<template>
  <div class="grid">
    <template v-for="meta in ROLES" :key="meta.key">
      <RoleCardSingle
        v-if="meta.singleBinding"
        :role="meta.key"
        :title="meta.title"
        :desc="meta.desc"
        :item="bindings[meta.key][0] ?? null"
        :services-empty="servicesEmpty"
        @add="(r) => emit('add', r)"
        @replace="emit('replace')"
        @remove="(i) => emit('remove', i)"
      />
      <RoleCardMulti
        v-else
        :role="meta.key"
        :title="meta.title"
        :desc="meta.desc"
        :items="bindings[meta.key]"
        :services-empty="servicesEmpty"
        @add="(r) => emit('add', r)"
        @reorder="(r, ordered) => emit('reorder', r, ordered)"
        @remove="(i) => emit('remove', i)"
        @edit="(i) => emit('edit', i)"
      />
    </template>
  </div>
</template>

<style lang="scss" scoped>
.grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: $space-4;

  @include respond-to(lg) {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
