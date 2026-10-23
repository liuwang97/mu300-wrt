# 重构交接（完成后删除本文件）

仓库：WSL `/home/akira/mu300-wrt`（还没有 remote；`/root/mu300-wrt` 是指向它的符号链接）。它是 dikeckaan/mu300-linux 的硬分叉，基线写在 `UPSTREAM` 里。
完整方案：`C:\Users\Akira\mu300-linux\U30Air-重构方案.md`。

## 用户已定的范围

- 单仓库两棵树：`kernel/`（Linux 7.2）和 `openwrt/`（OpenWrt-LuCI）。
- 只支持 F50 和 U30 Air，V50 删掉。
- 保留 mali、蓝牙、音频预留（补丁 0022）和 SD 卡逻辑。
- 不要安装器（install.*、gui、web、Magisk 全删）。
- LuCI 只留中文（zh_Hans）和英文（msgid 本身就是英文）。
- 其它支持一律删掉：5.4、6.18、7.3、Ubuntu、Arch、ImmortalWrt、plain openwrt。

## 已完成（4 个提交）

1. 用 filter-repo 生成新仓库，保留文件历史。已删：5.4 的 `kernel/`、Ubuntu 的 rootfs、arch、gui、site、wiki、docs、android/magisk、安装器、Magisk 和 GUI 测试、V50 驱动和补丁 0017、6.18 补丁目录、bring-up 工具、LuCI 的其它 29 种语言。
2. `kernel/build.sh`、`kernel/build-modules.sh` 已重写：
   - 版本从 `kernel/VERSION`（7.2.11）读取；
   - 不在 arm64 上时自动用 `aarch64-linux-gnu-` 交叉编译；
   - 不再依赖 `/work`、`/src` 符号链接（`MU300_SRC` 默认 `/src`，`MU300_OUT` 默认 `kernel/out`）。
3. `port/install.py` 和树外模块的 compat 代码只保留 7.x 分支。补丁目录已展平（0028 放在 `kernel/patches/`），`config-ignored.txt` 已删。
4. 全仓路径已按新布局替换。测试拆成 `kernel/tests` 和 `openwrt/tests`，`TOP` 仍是仓库根。

**这些都还没有构建或跑测试验证过。**

## 剩余工作（按顺序）

1. **跑通测试**。命令：`cd kernel/tests && python3 -m unittest`，`openwrt/tests` 同样。
   - 用 `/home/akira/bin/mu300-wrt-test.sh kernel|openwrt [模块...]`：以 akira 就地运行，设 `MU300_TEST_SHELLS='busybox sh'`，日志在 `~/mu300-test-results/`。
   - 旧仓库基线在 `/home/akira/mu300-base`（75295203），结果在 `/home/akira/mu300-base-tests/`。基线就有 5 个模块失败：
     - test_usb_pd：0028 编号重复，展平目录后应该会好；
     - test_terminal：root 权限导致；
     - test_sms_pool、test_mu300cell：时序问题；
     - test_device_scripts 的 Usb 用例。
   - 还没处理的引用：Python 里拼接路径的写法（例如 `TOP / 'android-vendor'`、`TOP / 'boot'`、`TOP / 'upstream'`），以及指向已删文件的测试。
   - 跨树的测试类要拆到对应的树：test_adb 里的 0027 部分、test_all_modules 的 AutoloadGuard 部分、test_wpa2_only 等。
2. **内核树**：
   - `make-bundle.sh`：不再从 5.4 包里取 busybox、logdw、e2fsck，改由新脚本 `kernel/initramfs/tools.sh` 构建：
     - busybox 用 Ubuntu 24.04 arm64 的 busybox-static；
     - e2fsck 用 `initramfs/e2fsck/build.sh`；
     - logdw 拷一份 37 行的源码到 `initramfs/`。
   - autoload-blocked 列表从 `openwrt/opt/mu300/lib/` 移到 `kernel/`，随 bundle 发布；`check-autoload-blocked.py` 在内核侧只查依赖闭包。
   - 删掉 `devices` 文件里 v50 的分支。
   - `initramfs/init`：删掉 V50（约 L103-124）和 5.4 的按设备模块（L125-133、L163、L493-499），保留 F50、U30 Air 和 SD 卡逻辑。
   - Dockerfile 保留。
3. **OpenWrt 树**：
   - `build-rootfs.sh`：只保留 openwrt-luci 和 openwrt 风味。删掉 `KREL=5.4`，不再强制要求 `out/modules`，镜像不带内核模块。删掉 ImmortalWrt、ujail 绕过（L222）、`19-mu300-fw4`。挂载路径改成 `openwrt/opt/mu300`。
   - `opt/mu300`：删掉只给 Ubuntu 用的脚本（hotspot-*、lan-start、kmsg-forward、rootfs-fixups、wcn-reopen 等），删之前先 grep 确认没有引用。删掉 V50 分支（mu300-sim L49-153）和散落的 5.4 分支。
   - `mu300-update`：
     - 删掉 Ubuntu 段（约 138 行）和多内核选择（L3022-3025、kernel_cmd）；
     - 默认内核改为 7.2；
     - 删掉 Magisk zip 离线更新，改成读 bundle 和 rootfs 包；
     - 更新源改到 fork。
   - LuCI：删掉语言页和 lang extra；只编 zh_Hans。
   - 修复 `cellparse.uc`：它已经移进 `openwrt/overlay`，需要确认构建时会被拷进镜像。
4. **根目录**：
   - `tools/make-release.sh` 重写，只出 7.2 bundle 和 openwrt-luci 镜像。
   - workflows 只保留 7.2，并加上 `tools/check-boundary`（两棵树互不引用）。
5. **文档**：
   - 写 `docs/contract.md`，接口内容见方案 3.3。
   - 写根目录、`kernel/`、`openwrt/` 三份 CLAUDE.md。
   - 从 `/home/akira/mu300-base/docs/FINDINGS.md` 蒸馏出三类文档：事实、决策（`docs/decisions/`）、未决问题（`docs/open-issues.md`）。
   - 把代码里的 `FINDINGS N` 引用改成一句 WHY，或者指向文档锚点。
   - 加 `tools/doc-lint`，进 CI。
6. **构建并对比**：
   - 内核在 WSL 原生编（参考 `/root/mu300-72-build.sh`）。本仓库用 `MU300_SRC=/home/akira/mu300src`：那里是签名校验过、未打补丁的 linux-7.2.11 和下载包；`/src`、`/work`、`/root/mu300src` 是其它会话共用的，不要在上面构建。
   - 镜像用 docker 编。**前提**：Docker Desktop 打开 Debian 的 WSL integration，否则 WSL 里没有 `docker`。
   - 和旧仓库的产物对比文件清单（`openwrt/tools/image-manifest.py`）。
   - 刷设备之前先读设备锁 `C:\Users\Akira\mu300-linux\U30AIR-DEVICE-LOCK.txt`。
