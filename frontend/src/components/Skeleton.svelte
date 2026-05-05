<script lang="ts">
  let {
    width = "100%",
    height = "14px",
    radius = "4px",
  }: {
    width?: string;
    height?: string;
    radius?: string;
  } = $props();
</script>

<span class="skel" style="display:block;width:{width};height:{height};border-radius:{radius}"
></span>
