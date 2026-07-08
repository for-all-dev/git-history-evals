/* libuv stubs. The wasm32 Lean runtime (libleanrt) references a handful of
 * libuv functions for temp-file/temp-dir IO, but the toolchain ships no wasm
 * libuv and our pure, IO-free ablation path never reaches them. We provide
 * link-satisfying stubs (pointer params are i32 in wasm, so `void*` matches the
 * real signatures); if ever called they fail cleanly rather than corrupt. */

#include <stddef.h>

const char *uv_strerror(int err) {
  (void)err;
  return "libuv unavailable in wasm build";
}

int uv_os_tmpdir(char *buffer, size_t *size) {
  (void)buffer; (void)size;
  return -1;  /* UV_* error */
}

int uv_fs_mkstemp(void *loop, void *req, const char *tpl, void *cb) {
  (void)loop; (void)req; (void)tpl; (void)cb;
  return -1;
}

int uv_fs_mkdtemp(void *loop, void *req, const char *tpl, void *cb) {
  (void)loop; (void)req; (void)tpl; (void)cb;
  return -1;
}
