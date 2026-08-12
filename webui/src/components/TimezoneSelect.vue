<script setup lang="ts">
import { NSelect } from "naive-ui";
import { computed } from "vue";

import {
  displayTimezone,
  setDisplayTimezone,
  timezoneFollowBrowser,
  timezoneOptions,
} from "@/state/timezone";

const options = computed(() => {
  const base = timezoneOptions();
  return [{ label: "跟随浏览器", value: "__browser__" }, ...base];
});

const model = computed({
  get: () => (timezoneFollowBrowser.value ? "__browser__" : displayTimezone.value),
  set: (v: string | null) => {
    if (!v || v === "__browser__") setDisplayTimezone(null);
    else setDisplayTimezone(v);
  },
});
</script>

<template>
  <div class="tz-wrap" title="显示时区（仅本机浏览器记住）">
    <span class="tz-label">时区</span>
    <n-select
      v-model:value="model"
      size="small"
      filterable
      tag
      clearable
      :options="options"
      :consistent-menu-width="false"
      :to="true"
      class="tz-select"
      placeholder="时区"
    />
  </div>
</template>

<style scoped>
.tz-wrap {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  /* Isolate from nav hit-testing / stacking */
  position: relative;
  z-index: 3;
}

.tz-label {
  font-size: 12px;
  color: var(--text-secondary, #64748b);
  white-space: nowrap;
  user-select: none;
}

.tz-select {
  width: 168px;
  min-width: 140px;
  max-width: 180px;
}
</style>
