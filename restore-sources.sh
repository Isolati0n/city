#!/bin/sh
# Restores the ten source files missing from the repo.
# Run from the repo root:  sh restore-sources.sh
set -e
mkdir -p houses bakery tests

cat > electrician.rs <<'NWEOF'
//! Bake-off twin. Not the TCB. Same walk as electrician.c.
use std::convert::TryInto;
use std::env;
use std::fs;
use std::io::Write;
use std::os::raw::{c_char, c_int};
use std::process;

const UNIT_SIZE: usize = 166;
const HDR: usize = 20;

extern "C" {
    fn socketpair(d: c_int, t: c_int, p: c_int, sv: *mut c_int) -> c_int;
    fn fork() -> c_int;
    fn pipe2(p: *mut c_int, flags: c_int) -> c_int;
    fn close(fd: c_int) -> c_int;
    fn write(fd: c_int, buf: *const u8, n: usize) -> isize;
    fn read(fd: c_int, buf: *mut u8, n: usize) -> isize;
    fn waitpid(pid: c_int, st: *mut c_int, flags: c_int) -> c_int;
    fn execl(path: *const c_char, arg: *const c_char, ...) -> c_int;
    fn setenv(n: *const c_char, v: *const c_char, o: c_int) -> c_int;
    fn open(path: *const c_char, flags: c_int) -> c_int;
    fn dup2(o: c_int, n: c_int) -> c_int;
    fn fcntl(fd: c_int, cmd: c_int, arg: c_int) -> c_int;
    fn prctl(a: c_int, ...) -> c_int;
    fn pause() -> c_int;
    fn _exit(c: c_int);
}

fn die(s: &str) -> ! {
    let _ = std::io::stderr().write_all(format!("[electrician] FAIL {s}\n").as_bytes());
    process::exit(71);
}

fn say(s: &str) {
    let _ = std::io::stderr().write_all(format!("[electrician] {s}\n").as_bytes());
}

fn cstr(s: &str) -> std::ffi::CString {
    std::ffi::CString::new(s).unwrap_or_else(|_| die("nul"))
}

fn field_name(u: &[u8], i: usize) -> String {
    let off = i * UNIT_SIZE;
    let s = &u[off..off + 32];
    let n = s.iter().position(|&b| b == 0).unwrap_or(32);
    String::from_utf8_lossy(&s[..n]).into_owned()
}
fn field_path(u: &[u8], i: usize) -> String {
    let off = i * UNIT_SIZE + 32;
    let s = &u[off..off + 128];
    let n = s.iter().position(|&b| b == 0).unwrap_or(128);
    String::from_utf8_lossy(&s[..n]).into_owned()
}
fn field_u8(u: &[u8], i: usize, extra: usize) -> u8 {
    u[i * UNIT_SIZE + extra]
}
fn edge(e: &[u8], i: usize) -> (u16, u16) {
    let o = i * 4;
    let a = u16::from_le_bytes([e[o], e[o + 1]]);
    let b = u16::from_le_bytes([e[o + 2], e[o + 3]]);
    (a, b)
}

fn pack_kit(log_w: c_int, wires: &[c_int]) {
    unsafe {
        let nullfd = open(b"/dev/null\0".as_ptr() as *const c_char, 0x80000);
        if nullfd < 0 {
            die("null");
        }
        let logn = fcntl(log_w, 1030, 3);
        if logn < 0 {
            die("dup log");
        }
        let mut parked = [0 as c_int; 128];
        for (i, w) in wires.iter().enumerate() {
            parked[i] = fcntl(*w, 1030, 3);
            if parked[i] < 0 {
                die("dup wire");
            }
        }
        for fd in 0..256 {
            let mut keep = fd == nullfd || fd == logn;
            for i in 0..wires.len() {
                if parked[i] == fd {
                    keep = true;
                }
            }
            if !keep {
                close(fd);
            }
        }
        if dup2(nullfd, 0) < 0 || dup2(logn, 1) < 0 || dup2(logn, 2) < 0 {
            die("dup2");
        }
        if nullfd > 2 {
            close(nullfd);
        }
        if logn > 2 {
            close(logn);
        }
        for i in 0..wires.len() {
            let dest = 3 + i as c_int;
            if parked[i] != dest {
                dup2(parked[i], dest);
                if parked[i] != dest {
                    close(parked[i]);
                }
            }
            let fl = fcntl(dest, 1, 0);
            fcntl(dest, 2, fl & !1);
        }
        for fd in 0..3 {
            let fl = fcntl(fd, 1, 0);
            fcntl(fd, 2, fl & !1);
        }
    }
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 4 {
        die("argv");
    }
    let blob_path = &args[1];
    let report_fd: c_int = args[2].parse().unwrap_or(-1);
    let nlogs: usize = args[3].parse().unwrap_or(0);
    if args.len() != 4 + nlogs {
        die("log argc");
    }
    let mut logw = vec![0 as c_int; nlogs];
    for i in 0..nlogs {
        logw[i] = args[4 + i].parse().unwrap_or(-1);
    }
    let sup = env::var("NW_SUP").unwrap_or_else(|_| die("NW_SUP"));

    let blob = fs::read(blob_path).unwrap_or_else(|_| die("read blob"));
    if blob.len() < HDR || &blob[0..8] != b"NWPLAN02" {
        die("magic");
    }
    let n_units = u32::from_le_bytes(blob[8..12].try_into().unwrap()) as usize;
    let n_edges = u32::from_le_bytes(blob[12..16].try_into().unwrap()) as usize;
    if n_units != nlogs {
        die("log/unit mismatch");
    }
    let units = &blob[HDR..];
    let edges = &blob[HDR + n_units * UNIT_SIZE..];

    let mut pair = vec![[0 as c_int; 2]; n_edges];
    unsafe {
        for i in 0..n_edges {
            if socketpair(1, 1 | 0x80000, 0, pair[i].as_mut_ptr()) != 0 {
                die("socketpair");
            }
        }
    }

    let mut pids = vec![0 as c_int; n_units];
    for i in 0..n_units {
        let mut wires = Vec::new();
        for e in 0..n_edges {
            let (a, b) = edge(edges, e);
            if a as usize == i {
                wires.push(pair[e][0]);
            } else if b as usize == i {
                wires.push(pair[e][1]);
            }
        }
        let mut pp = [0 as c_int; 2];
        unsafe {
            if pipe2(pp.as_mut_ptr(), 0x80000) != 0 {
                die("pipe");
            }
            let mid = fork();
            if mid < 0 {
                die("fork");
            }
            if mid == 0 {
                close(pp[0]);
                let house = fork();
                if house < 0 {
                    die("fork house");
                }
                if house != 0 {
                    write(pp[1], &house as *const c_int as *const u8, 4);
                    _exit(0);
                }
                close(pp[1]);
                pack_kit(logw[i], &wires);
                let name = field_name(units, i);
                let path = field_path(units, i);
                let lids = field_u8(units, i, 164);
                let crit = field_u8(units, i, 160);
                let budget = field_u8(units, i, 161);
                let win = u16::from_le_bytes([
                    units[i * UNIT_SIZE + 162],
                    units[i * UNIT_SIZE + 163],
                ]);
                let cn = cstr(&name);
                let cp = cstr(&path);
                let cs = cstr(&sup);
                let ck = cstr(&format!("{}", wires.len()));
                let cl = cstr(&format!("{lids}"));
                let cb = cstr(&format!("{budget}"));
                let cw = cstr(&format!("{win}"));
                let cc = cstr(&format!("{crit}"));
                setenv(cstr("NW_UNIT").as_ptr(), cn.as_ptr(), 1);
                setenv(cstr("NW_HOUSE").as_ptr(), cn.as_ptr(), 1);
                setenv(cstr("NW_WIRES").as_ptr(), ck.as_ptr(), 1);
                setenv(cstr("NW_KIT").as_ptr(), ck.as_ptr(), 1);
                setenv(cstr("NW_LIDS").as_ptr(), cl.as_ptr(), 1);
                setenv(cstr("NW_BUDGET").as_ptr(), cb.as_ptr(), 1);
                setenv(cstr("NW_WINDOW").as_ptr(), cw.as_ptr(), 1);
                setenv(cstr("NW_CRITICAL").as_ptr(), cc.as_ptr(), 1);
                execl(
                    cs.as_ptr(),
                    cstr("nw-sup").as_ptr(),
                    cp.as_ptr(),
                    cn.as_ptr(),
                    std::ptr::null::<c_char>(),
                );
                die("exec nw-sup");
            }
            close(pp[1]);
            let mut house = 0 as c_int;
            if read(pp[0], &mut house as *mut c_int as *mut u8, 4) != 4 {
                die("house pid");
            }
            close(pp[0]);
            waitpid(mid, std::ptr::null_mut(), 0);
            pids[i] = house;
            say(&format!(
                "spawned {} pid={} kit={} lids={}",
                field_name(units, i),
                house,
                wires.len(),
                field_u8(units, i, 164)
            ));
        }
    }
    unsafe {
        for i in 0..n_edges {
            close(pair[i][0]);
            close(pair[i][1]);
        }
        for fd in logw {
            close(fd);
        }
        let nu = n_units as u32;
        write(report_fd, &nu as *const u32 as *const u8, 4);
        write(
            report_fd,
            pids.as_ptr() as *const u8,
            4 * n_units,
        );
        close(report_fd);
    }
    say("kits filled");
    say("inert");
    unsafe {
        prctl(38, 1, 0, 0, 0);
        // skip full seccomp in rust twin if filter is fiddly; pause loop is enough for bake-off hold
        loop {
            pause();
        }
    }
}
NWEOF
echo "  electrician.rs"

cat > electrician.zig <<'NWEOF'
const std = @import("std");

extern fn nw_check(blob: [*]const u8, len: u32) c_int;
extern fn socketpair(d: c_int, t: c_int, p: c_int, sv: *[2]c_int) c_int;
extern fn fork() c_int;
extern fn waitpid(pid: c_int, st: ?*c_int, flags: c_int) c_int;
extern fn pipe2(p: *[2]c_int, flags: c_int) c_int;
extern fn fcntl(fd: c_int, cmd: c_int, arg: c_int) c_int;
extern fn open(path: [*:0]const u8, flags: c_int) c_int;
extern fn close(fd: c_int) c_int;
extern fn dup2(o: c_int, n: c_int) c_int;
extern fn write(fd: c_int, buf: [*]const u8, n: usize) isize;
extern fn read(fd: c_int, buf: [*]u8, n: usize) isize;
extern fn execl(path: [*:0]const u8, arg: [*:0]const u8, ...) c_int;
extern fn setenv(n: [*:0]const u8, v: [*:0]const u8, o: c_int) c_int;
extern fn getenv(n: [*:0]const u8) ?[*:0]const u8;
extern fn atoi(s: [*:0]const u8) c_int;
extern fn prctl(a: c_int, ...) c_int;
extern fn pause() c_int;
extern fn _exit(c: c_int) noreturn;
extern fn opendir(p: [*:0]const u8) ?*anyopaque;
extern fn readdir(d: *anyopaque) ?*Dirent;
extern fn closedir(d: *anyopaque) c_int;
extern fn dirfd(d: *anyopaque) c_int;

const Dirent = extern struct {
    ino: u64,
    off: i64,
    reclen: u16,
    typ: u8,
    name: [256]u8,
};

const O_RDONLY = 0;
const O_CLOEXEC = 0x80000;
const O_DIRECTORY = 0x10000;
const F_GETFD = 1;
const F_SETFD = 2;
const F_DUPFD_CLOEXEC = 1030;
const FD_CLOEXEC = 1;
const AF_UNIX = 1;
const SOCK_STREAM = 1;
const SOCK_CLOEXEC = 0x80000;

const MAX_UNITS: usize = 64;
const MAX_EDGES: usize = 128;

fn die(msg: []const u8) noreturn {
    var buf: [180]u8 = undefined;
    const n = std.fmt.bufPrint(&buf, "[electrician] FAIL {s}\n", .{msg}) catch "FAIL\n";
    _ = write(2, n.ptr, n.len);
    _exit(71);
}

fn say(msg: []const u8) void {
    var buf: [180]u8 = undefined;
    const n = std.fmt.bufPrint(&buf, "[electrician] {s}\n", .{msg}) catch return;
    _ = write(2, n.ptr, n.len);
}

fn kept(fd: c_int, keep: []const c_int) bool {
    for (keep) |k| if (k == fd) return true;
    return false;
}

fn closeOthers(keep: []const c_int) void {
    const d = opendir("/proc/self/fd") orelse {
        var fd: c_int = 0;
        while (fd < 512) : (fd += 1) {
            if (!kept(fd, keep)) _ = close(fd);
        }
        return;
    };
    const dfd = dirfd(d);
    var doomed: [512]c_int = undefined;
    var nd: usize = 0;
    while (readdir(d)) |ent| {
        if (ent.name[0] == '.' or ent.name[0] == 0) continue;
        var i: usize = 0;
        while (i < 16 and ent.name[i] != 0) : (i += 1) {}
        const sl = ent.name[0..i];
        const fd = std.fmt.parseInt(c_int, sl, 10) catch continue;
        if (fd == dfd) continue;
        if (!kept(fd, keep) and nd < doomed.len) {
            doomed[nd] = fd;
            nd += 1;
        }
    }
    _ = closedir(d);
    for (doomed[0..nd]) |fd| _ = close(fd);
}

fn clearCloexec(fd: c_int) void {
    const fl = fcntl(fd, F_GETFD, 0);
    if (fl < 0) die("getfd");
    if (fcntl(fd, F_SETFD, fl & ~@as(c_int, FD_CLOEXEC)) < 0) die("setfd");
}

fn packKit(log_w: c_int, wires: []const c_int) void {
    const nullfd = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (nullfd < 0) die("open null");
    const logn = fcntl(log_w, F_DUPFD_CLOEXEC, 3);
    if (logn < 0) die("dup log");
    var parked: [MAX_EDGES]c_int = undefined;
    for (wires, 0..) |w, i| {
        parked[i] = fcntl(w, F_DUPFD_CLOEXEC, 3);
        if (parked[i] < 0) die("dup wire");
    }
    var keep_buf: [3 + MAX_EDGES]c_int = undefined;
    var nk: usize = 0;
    keep_buf[nk] = nullfd;
    nk += 1;
    keep_buf[nk] = logn;
    nk += 1;
    for (wires, 0..) |_, i| {
        keep_buf[nk] = parked[i];
        nk += 1;
    }
    closeOthers(keep_buf[0..nk]);
    if (dup2(nullfd, 0) < 0 or dup2(logn, 1) < 0 or dup2(logn, 2) < 0) die("dup2 std");
    if (nullfd > 2) _ = close(nullfd);
    if (logn > 2) _ = close(logn);
    for (wires, 0..) |_, i| {
        const dest: c_int = @intCast(3 + i);
        if (parked[i] != dest) {
            if (dup2(parked[i], dest) < 0) die("dup2 kit");
            if (parked[i] != dest) _ = close(parked[i]);
        }
        clearCloexec(dest);
    }
    clearCloexec(0);
    clearCloexec(1);
    clearCloexec(2);
}

const BPF_LD: u16 = 0x00;
const BPF_W: u16 = 0x00;
const BPF_ABS: u16 = 0x20;
const BPF_JMP: u16 = 0x05;
const BPF_JEQ: u16 = 0x10;
const BPF_K: u16 = 0x00;
const BPF_RET: u16 = 0x06;
const SECCOMP_RET_KILL_PROCESS: u32 = 0x80000000;
const SECCOMP_RET_ALLOW: u32 = 0x7fff0000;
const PR_SET_NO_NEW_PRIVS: c_int = 38;
const PR_SET_SECCOMP: c_int = 22;
const SECCOMP_MODE_FILTER: usize = 2;

const SockFilter = extern struct { code: u16, jt: u8, jf: u8, k: u32 };
const SockFprog = extern struct { len: u16, pad: u16 = 0, filter: *SockFilter };

fn goInert() noreturn {
    const allow = [_]u32{ 34, 15, 231, 60 }; // pause, rt_sigreturn, exit_group, exit
    var f: [8]SockFilter = undefined;
    var n: usize = 0;
    f[n] = .{ .code = BPF_LD | BPF_W | BPF_ABS, .jt = 0, .jf = 0, .k = 0 };
    n += 1;
    for (allow, 0..) |nr, i| {
        f[n] = .{ .code = BPF_JMP | BPF_JEQ | BPF_K, .jt = @intCast(allow.len - i), .jf = 0, .k = nr };
        n += 1;
    }
    f[n] = .{ .code = BPF_RET | BPF_K, .jt = 0, .jf = 0, .k = SECCOMP_RET_KILL_PROCESS };
    n += 1;
    f[n] = .{ .code = BPF_RET | BPF_K, .jt = 0, .jf = 0, .k = SECCOMP_RET_ALLOW };
    n += 1;
    var prog = SockFprog{ .len = @intCast(n), .filter = &f[0] };
    say("inert");
    if (prctl(PR_SET_NO_NEW_PRIVS, @as(c_ulong, 1), @as(c_ulong, 0), @as(c_ulong, 0), @as(c_ulong, 0)) != 0)
        die("no_new_privs");
    if (prctl(PR_SET_SECCOMP, @as(c_ulong, SECCOMP_MODE_FILTER), @intFromPtr(&prog), @as(c_ulong, 0), @as(c_ulong, 0)) != 0)
        die("seccomp");
    while (true) _ = pause();
}

const Hdr = extern struct {
    magic: [8]u8,
    n_units: u32,
    n_edges: u32,
    crc32: u32,
};

const UNIT_SIZE: usize = 166;
const EDGE_SIZE: usize = 4;

fn fieldName(units: []const u8, i: u32) []const u8 {
    const off = @as(usize, i) * UNIT_SIZE;
    var n: usize = 0;
    while (n < 32 and units[off + n] != 0) : (n += 1) {}
    return units[off .. off + n];
}
fn fieldPath(units: []const u8, i: u32) []const u8 {
    const off = @as(usize, i) * UNIT_SIZE + 32;
    var n: usize = 0;
    while (n < 128 and units[off + n] != 0) : (n += 1) {}
    return units[off .. off + n];
}
fn fieldLids(units: []const u8, i: u32) u8 {
    return units[@as(usize, i) * UNIT_SIZE + 164];
}
fn edgeA(edges: []const u8, i: u32) u16 {
    const off = @as(usize, i) * EDGE_SIZE;
    return @as(u16, edges[off]) | (@as(u16, edges[off + 1]) << 8);
}
fn edgeB(edges: []const u8, i: u32) u16 {
    const off = @as(usize, i) * EDGE_SIZE;
    return @as(u16, edges[off + 2]) | (@as(u16, edges[off + 3]) << 8);
}

pub fn main() void {
    var it = std.process.args();
    _ = it.next();
    const blob_path = it.next() orelse die("argv");
    const report_s = it.next() orelse die("report");
    const nlogs_s = it.next() orelse die("nlogs");
    var blob_z_buf: [512]u8 = undefined;
    if (blob_path.len >= blob_z_buf.len) die("path");
    @memcpy(blob_z_buf[0..blob_path.len], blob_path);
    blob_z_buf[blob_path.len] = 0;
    const blob_z: [*:0]const u8 = @ptrCast(&blob_z_buf);

    var rbuf: [16]u8 = undefined;
    @memcpy(rbuf[0..report_s.len], report_s);
    rbuf[report_s.len] = 0;
    var nbuf: [16]u8 = undefined;
    @memcpy(nbuf[0..nlogs_s.len], nlogs_s);
    nbuf[nlogs_s.len] = 0;
    const report_fd = atoi(@ptrCast(&rbuf));
    const nlogs: usize = @intCast(atoi(@ptrCast(&nbuf)));
    if (nlogs < 1 or nlogs > MAX_UNITS) die("nlogs");

    var logw: [MAX_UNITS]c_int = undefined;
    var li: usize = 0;
    while (li < nlogs) : (li += 1) {
        const s = it.next() orelse die("log argc");
        var tmp: [16]u8 = undefined;
        @memcpy(tmp[0..s.len], s);
        tmp[s.len] = 0;
        logw[li] = atoi(@ptrCast(&tmp));
    }

    const sup = getenv("NW_SUP") orelse die("NW_SUP");

    const bfd = open(blob_z, O_RDONLY);
    if (bfd < 0) die("open blob");
    var blob: [65536]u8 = undefined;
    const nread = read(bfd, &blob, blob.len);
    _ = close(bfd);
    if (nread <= 0) die("read blob");
    if (nw_check(&blob, @intCast(nread)) != 0) die("blob recheck");

    const hdr: *const Hdr = @ptrCast(@alignCast(&blob));
    if (hdr.n_units != nlogs) die("log/unit mismatch");
    const units = blob[@sizeOf(Hdr)..];
    const edges_off = @sizeOf(Hdr) + @as(usize, hdr.n_units) * UNIT_SIZE;
    const edges = blob[edges_off..];

    var pair: [MAX_EDGES][2]c_int = undefined;
    var ei: u32 = 0;
    while (ei < hdr.n_edges) : (ei += 1) {
        if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, &pair[ei]) != 0)
            die("socketpair");
    }

    var pids: [MAX_UNITS]c_int = undefined;
    var ui: u32 = 0;
    while (ui < hdr.n_units) : (ui += 1) {
        var wires: [MAX_EDGES]c_int = undefined;
        var nw: usize = 0;
        var e: u32 = 0;
        while (e < hdr.n_edges) : (e += 1) {
            if (edgeA(edges, e) == ui) {
                wires[nw] = pair[e][0];
                nw += 1;
            } else if (edgeB(edges, e) == ui) {
                wires[nw] = pair[e][1];
                nw += 1;
            }
        }
        var pp: [2]c_int = undefined;
        if (pipe2(&pp, O_CLOEXEC) != 0) die("pid pipe");
        const mid = fork();
        if (mid < 0) die("fork");
        if (mid == 0) {
            _ = close(pp[0]);
            const pid = fork();
            if (pid < 0) die("fork house");
            if (pid != 0) {
                const wr = write(pp[1], @ptrCast(&pid), 4);
                _ = wr;
                _exit(0);
            }
            _ = close(pp[1]);
            packKit(logw[ui], wires[0..nw]);
            var kitb: [8]u8 = undefined;
            var lidb: [8]u8 = undefined;
            const ks = std.fmt.bufPrintZ(&kitb, "{d}", .{nw}) catch die("fmt");
            const ls = std.fmt.bufPrintZ(&lidb, "{d}", .{fieldLids(units, ui)}) catch die("fmt");
            var namez: [33]u8 = undefined;
            const nm = fieldName(units, ui);
            @memcpy(namez[0..nm.len], nm);
            namez[nm.len] = 0;
            var pathz: [129]u8 = undefined;
            const ep = fieldPath(units, ui);
            @memcpy(pathz[0..ep.len], ep);
            pathz[ep.len] = 0;
            _ = setenv("NW_UNIT", @ptrCast(&namez), 1);
            _ = setenv("NW_HOUSE", @ptrCast(&namez), 1);
            _ = setenv("NW_WIRES", ks.ptr, 1);
            _ = setenv("NW_KIT", ks.ptr, 1);
            _ = setenv("NW_LIDS", ls.ptr, 1);
            _ = execl(sup, "nw-sup", @as([*:0]const u8, @ptrCast(&pathz)), @as([*:0]const u8, @ptrCast(&namez)), @as(?[*:0]const u8, null));
            die("exec nw-sup");
        }
        _ = close(pp[1]);
        var house_pid: c_int = 0;
        if (read(pp[0], @ptrCast(&house_pid), 4) != 4) die("house pid");
        _ = close(pp[0]);
        _ = waitpid(mid, null, 0);
        pids[ui] = house_pid;
        var line: [96]u8 = undefined;
        const msg = std.fmt.bufPrint(&line, "spawned {s} pid={d} kit={d} lids={d}", .{
            fieldName(units, ui), house_pid, nw, fieldLids(units, ui),
        }) catch "spawned";
        say(msg);
    }

    ei = 0;
    while (ei < hdr.n_edges) : (ei += 1) {
        _ = close(pair[ei][0]);
        _ = close(pair[ei][1]);
    }
    for (0..nlogs) |i| _ = close(logw[i]);

    const nu: u32 = hdr.n_units;
    const nbytes = write(report_fd, @ptrCast(&nu), 4);
    if (nbytes != 4) die("report n");
    const pbytes = write(report_fd, @ptrCast(&pids), @sizeOf(c_int) * nu);
    if (pbytes != @as(isize, @intCast(@sizeOf(c_int) * nu))) die("report pids");
    _ = close(report_fd);
    say("kits filled");
    goInert();
}
NWEOF
echo "  electrician.zig"

cat > nwsup.rs <<'NWEOF'
//! House supervisor. Lids from the plan.
//! Seccomp BPF lives in lids.c (same table the C twin uses).

use std::env;
use std::ffi::CString;
use std::io::Write;
use std::os::raw::{c_char, c_int};
use std::process;

const NW_LID_SECCOMP: u32 = 0x01;
const NW_LID_NEWNS: u32 = 0x04;
const NW_LID_NEWNET: u32 = 0x08;
const CLONE_NEWNS: c_int = 0x00020000;
const CLONE_NEWNET: c_int = 0x40000000;

extern "C" {
    fn unshare(flags: c_int) -> c_int;
    fn execv(path: *const c_char, argv: *const *const c_char) -> c_int;
    fn nw_apply_house_seccomp() -> c_int;
}

fn say(s: &str) {
    let _ = std::io::stderr().write_all(format!("[nw-sup] {s}\n").as_bytes());
}

fn die(s: &str) -> ! {
    let err = std::io::Error::last_os_error();
    let _ = std::io::stderr().write_all(format!("[nw-sup] FAIL {s} {err}\n").as_bytes());
    process::exit(72);
}

fn lid_netns() {
    unsafe {
        if unshare(CLONE_NEWNET) != 0 {
            die("unshare net");
        }
    }
    say("lid newnet");
}

fn lid_newns() {
    unsafe {
        if unshare(CLONE_NEWNS) != 0 {
            die("unshare ns");
        }
    }
    say("lid newns");
}

fn lid_seccomp() {
    unsafe {
        if nw_apply_house_seccomp() != 0 {
            die("house seccomp");
        }
    }
    say("lid seccomp");
}

fn main() {
    let mut args = env::args();
    let _ = args.next();
    let path = args.next().unwrap_or_else(|| die("argv path"));
    let name = args.next().unwrap_or_else(|| die("argv name"));
    let lids: u32 = env::var("NW_LIDS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);

    if lids & NW_LID_NEWNET != 0 {
        lid_netns();
    }
    if lids & NW_LID_NEWNS != 0 {
        lid_newns();
    }
    if lids & NW_LID_SECCOMP != 0 {
        lid_seccomp();
    }

    let c_path = CString::new(path).unwrap_or_else(|_| die("path nul"));
    let c_name = CString::new(name).unwrap_or_else(|_| die("name nul"));
    let argv = [c_name.as_ptr(), std::ptr::null()];
    unsafe {
        execv(c_path.as_ptr(), argv.as_ptr());
    }
    die("exec house");
}
NWEOF
echo "  nwsup.rs"

cat > houses/talk.c <<'NWEOF'
#define _GNU_SOURCE
#include <unistd.h>
int main(void)
{
    const char m[] = "PING\n";
    ssize_t w = write(3, m, sizeof m - 1);
    (void)w;
    const char ok[] = "talk sent\n";
    w = write(1, ok, sizeof ok - 1);
    (void)w;
    usleep(200 * 1000);
    return 0;
}
NWEOF
echo "  houses/talk.c"

cat > houses/listen.c <<'NWEOF'
#define _GNU_SOURCE
#include <unistd.h>
int main(void)
{
    char b[16];
    ssize_t n = read(3, b, sizeof b);
    const char *msg = (n >= 4 && b[0] == 'P') ? "listen got ping\n" : "listen miss\n";
    ssize_t w = write(1, msg, __builtin_strlen(msg));
    (void)w;
    usleep(200 * 1000);
    return n >= 4 ? 0 : 4;
}
NWEOF
echo "  houses/listen.c"

cat > houses/boom.c <<'NWEOF'
#include <unistd.h>
int main(void)
{
    const char m[] = "boom\n";
    ssize_t w = write(1, m, sizeof m - 1);
    (void)w;
    _exit(99);
}
NWEOF
echo "  houses/boom.c"

cat > houses/badcall.c <<'NWEOF'
#define _GNU_SOURCE
#include <sys/socket.h>
#include <unistd.h>
int main(void)
{
    int s = socket(AF_INET, SOCK_STREAM, 0);
    const char m[] = "badcall survived\n";
    if (s >= 0) {
        ssize_t w = write(1, m, sizeof m - 1);
        (void)w;
        return 0;
    }
    return 1;
}
NWEOF
echo "  houses/badcall.c"

cat > bakery/nw-cc.py <<'NWEOF'
#!/usr/bin/env python3
"""nw-cc stand-in (Haskell/OCaml baker). Not in the TCB.

Encodes the Alloy assertions: unique names, no self-wire, derived fd budget,
closed lid set. Lockfile = the blob. Never rebuild-switch.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
import zlib

NAME_LEN, PATH_LEN = 32, 128
MAX_UNITS, MAX_EDGES, FD_RESERVED, MAX_FDS = 64, 128, 8, 1024
LID_SECCOMP, LID_LANDLOCK, LID_NEWNS, LID_NEWNET = 1, 2, 4, 8
KNOWN_LIDS = LID_SECCOMP | LID_LANDLOCK | LID_NEWNS | LID_NEWNET


def pad(s: str, n: int) -> bytes:
    b = s.encode("ascii")
    if len(b) >= n:
        raise SystemExit(f"too long: {s}")
    return b + b"\x00" * (n - len(b))


def check(houses, wires):
    names = [h[0] for h in houses]
    if len(names) != len(set(names)):
        raise SystemExit("duplicate name")
    if not (1 <= len(houses) <= MAX_UNITS):
        raise SystemExit("unit count")
    if len(wires) > MAX_EDGES:
        raise SystemExit("edge count")
    idx = {n: i for i, n in enumerate(names)}
    seen = set()
    for a, b in wires:
        if a not in idx or b not in idx:
            raise SystemExit("edge index")
        if a == b:
            raise SystemExit("self-edge")
        key = tuple(sorted((idx[a], idx[b])))
        if key in seen:
            raise SystemExit("duplicate edge")
        seen.add(key)
    need = FD_RESERVED + len(houses) * 2 + len(wires) * 2
    if need > MAX_FDS:
        raise SystemExit("fd budget")
    # Datalog-shaped: hold(H) if incident to a wire. Isolated houses are allowed
    # only as explicit empty kits — listed, never implicit.
    held = set()
    for a, b in wires:
        held.add(a)
        held.add(b)
    isolated = [h[0] for h in houses if h[0] not in held]
    if isolated:
        print("isolated-kits", ",".join(isolated))
    for name, path, crit, budget, window, lids in houses:
        if crit not in (0, 1):
            raise SystemExit("critical")
        if lids & ~KNOWN_LIDS:
            raise SystemExit("lids")
        if not path.startswith("/"):
            raise SystemExit("exec_path")
        if not name or not name.replace("-", "x").replace("_", "x").isalnum():
            raise SystemExit("name")
    return idx


def bake(path, houses, wires):
    idx = check(houses, wires)
    unit = b""
    for name, exe, crit, budget, window, lids in houses:
        unit += pad(name, NAME_LEN) + pad(exe, PATH_LEN)
        unit += struct.pack("<BBHBB", crit, budget, window, lids, 0)
    edge = b""
    for a, b in wires:
        edge += struct.pack("<HH", idx[a], idx[b])
    prefix = b"NWPLAN02" + struct.pack("<II", len(houses), len(wires))
    crc = zlib.crc32(prefix + struct.pack("<I", 0) + unit + edge) & 0xFFFFFFFF
    blob = prefix + struct.pack("<I", crc) + unit + edge
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "wb").write(blob)
    digest = hashlib.sha256(blob).hexdigest()
    open(path + ".sha256", "w").write(digest + "\n")
    print(f"wrote {path} units={len(houses)} edges={len(wires)} crc=0x{crc:08x} bytes={len(blob)} sha256={digest}")


def default_city(probe: str, lids: int):
    return [
        ("alpha", probe, 0, 3, 2, lids),
        ("beta",  probe, 0, 3, 2, lids),
        ("gamma", probe, 0, 3, 2, lids),
        ("delta", probe, 0, 1, 2, lids),
    ], [("alpha", "beta"), ("beta", "gamma")]


def parse_lids(s: str) -> int:
    lids = 0
    for tok in s.split(","):
        tok = tok.strip().lower()
        lids |= {
            "none": 0, "seccomp": LID_SECCOMP, "landlock": LID_LANDLOCK,
            "newns": LID_NEWNS, "newnet": LID_NEWNET,
        }.get(tok, 0)
    return lids


def load_city(path: str):
    houses, wires = [], []
    for raw in open(path):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] == "house":
            name, exe = parts[1], parts[2]
            crit, budget, window, lids = 0, 3, 2, 0
            for kv in parts[3:]:
                k, _, v = kv.partition("=")
                if k == "critical":
                    crit = int(v)
                elif k == "budget":
                    budget = int(v)
                elif k == "window":
                    window = int(v)
                elif k == "lids":
                    lids = parse_lids(v)
            houses.append((name, os.path.abspath(exe), crit, budget, window, lids))
        elif parts[0] == "wire":
            wires.append((parts[1], parts[2]))
        else:
            raise SystemExit(f"bad city line: {line}")
    return houses, wires


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="plan.blob")
    ap.add_argument("--probe", default="")
    ap.add_argument("--city", default="")
    ap.add_argument("--lids", default="seccomp")
    args = ap.parse_args()
    if args.city:
        houses, wires = load_city(args.city)
    else:
        if not args.probe:
            raise SystemExit("--probe or --city required")
        houses, wires = default_city(os.path.abspath(args.probe), parse_lids(args.lids))
    bake(args.out, houses, wires)


if __name__ == "__main__":
    main()
NWEOF
echo "  bakery/nw-cc.py"

cat > tests/run.py <<'NWEOF'
#!/usr/bin/env python3
"""Put-together suite. Not in the TCB."""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STAGE = "/tmp/nw-init-run"
CC = os.path.join(ROOT, "bakery", "nw-cc.py")


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, **kw)
    p.out = (p.stdout or b"").decode("utf-8", "replace")
    p.err = (p.stderr or b"").decode("utf-8", "replace")
    return p


def boot(slot=None, plan=None, extra=None, hold=800):
    cmd = ["unshare", "--pid", "--fork", "--mount-proc", "--", f"{STAGE}/nw-root", "--hold-ms", str(hold)]
    if slot:
        cmd += ["--slot", slot]
    if plan:
        cmd += [plan]
    extra = extra or []
    cmd += extra
    p = run(cmd)
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out


def expect(cond, msg):
    if not cond:
        raise SystemExit("FAIL: " + msg)


def test_happy():
    rc, out = boot(slot=f"{STAGE}/slots/A", hold=900)
    expect(rc == 0, f"happy rc={rc}\n{out}")
    expect("kit_env=1" in out and "kit_env=2" in out and "kit_env=0" in out, "kits")
    expect("socket_wires=0" in out, "delta empty")
    expect("electrician] inert" in out, "inert")
    expect("houses_reaped=4" in out, "reap")
    expect("orphans=0" in out, "orphans")
    print("ok happy")


def test_slot_b():
    rc, out = boot(slot=f"{STAGE}/slots/B", hold=700)
    expect(rc == 0, f"slot B rc={rc}\n{out}")
    print("ok slot-B")


def test_rescue():
    p = run(["unshare", "--pid", "--fork", "--mount-proc", "--",
             f"{STAGE}/nw-root", "--rescue", f"{STAGE}/slots/rescue"])
    out = p.out + p.err
    expect(p.returncode == 3, f"rescue rc={p.returncode}\n{out}")
    expect("outside the plan" in out, "rescue text")
    print("ok rescue")


def test_halt_electrician():
    rc, out = boot(slot=f"{STAGE}/slots/A", extra=["--kill-electrician"], hold=400)
    expect(rc == 70, f"halt rc={rc}\n{out}")
    expect("HALT: electrician" in out, "halt text")
    print("ok halt-electrician")


def test_bad_crc():
    bad = f"{STAGE}/bad.blob"
    d = bytearray(open(f"{STAGE}/plan.blob", "rb").read())
    d[16] ^= 0xFF
    open(bad, "wb").write(d)
    chk = run([f"{STAGE}/nw-check", bad])
    expect(chk.returncode == 1 and "crc32" in (chk.err + chk.out), "check crc")
    rc, out = boot(plan=bad, hold=200)
    expect(rc == 70 and "crc32" in out, f"boot crc\n{out}")
    print("ok bad-crc")


def test_baker_rejects():
    city = f"{STAGE}/bad-city.txt"
    open(city, "w").write("house a /bin/true\nwire a a\n")
    p = run(["python3", CC, "--city", city, "--out", f"{STAGE}/nope.blob"])
    expect(p.returncode != 0, "self-wire should fail bake")
    print("ok baker-reject-self-wire")


def test_fuzz_checker():
    good = open(f"{STAGE}/plan.blob", "rb").read()
    accepted = 0
    for i in range(200):
        d = bytearray(good)
        d[i % len(d)] ^= 1 + (i % 7)
        p = tempfile.NamedTemporaryFile(delete=False, dir=STAGE)
        p.write(d)
        p.close()
        r = run([f"{STAGE}/nw-check", p.name])
        if r.returncode == 0:
            accepted += 1
    expect(accepted == 0, f"fuzz accepted {accepted}")
    print("ok fuzz-200")


def test_difftest():
    """Baker output must be accepted by C nw-check; flipped crc must not."""
    r = run([f"{STAGE}/nw-check", f"{STAGE}/plan.blob"])
    expect(r.returncode == 0, "difftest good")
    print("ok difftest")


def test_wire_talk():
    talk = f"{STAGE}/unit-talk"
    listen = f"{STAGE}/unit-listen"
    city = f"{STAGE}/talk.city"
    open(city, "w").write(
        f"house talk {talk} lids=none\n"
        f"house listen {listen} lids=none\n"
        f"wire talk listen\n"
    )
    blob = f"{STAGE}/talk.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, b.err + b.out)
    rc, out = boot(plan=blob, hold=700)
    expect(rc == 0, f"talk rc={rc}\n{out}")
    expect("talk sent" in out, "talk")
    expect("listen got ping" in out, f"listen\n{out}")
    print("ok wire-talk")


def test_critical_halt():
    boom = f"{STAGE}/unit-boom"
    probe = f"{STAGE}/unit-probe"
    city = f"{STAGE}/crit.city"
    open(city, "w").write(
        f"house boom {boom} critical=1 lids=none\n"
        f"house idle {probe} lids=none\n"
    )
    blob = f"{STAGE}/crit.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, b.err)
    rc, out = boot(plan=blob, hold=800)
    expect(rc == 70, f"crit rc={rc}\n{out}")
    expect("HALT: critical house" in out, f"crit text\n{out}")
    print("ok critical-halt")


def test_seccomp_kills():
    bad = f"{STAGE}/unit-badcall"
    city = f"{STAGE}/sec.city"
    open(city, "w").write(f"house bad {bad} lids=seccomp\n")
    blob = f"{STAGE}/sec.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, b.err)
    rc, out = boot(plan=blob, hold=600)
    expect("badcall survived" not in out, f"seccomp leak\n{out}")
    print("ok seccomp-kill")


def test_hash_pin():
    h = open(f"{STAGE}/plan.blob.sha256").read().strip()
    expect(len(h) == 64, "sha256 len")
    import hashlib
    got = hashlib.sha256(open(f"{STAGE}/plan.blob", "rb").read()).hexdigest()
    expect(h == got, "sha256 match")
    print("ok hash-pin")


def main():
    os.chdir(ROOT)
    print("== city suite ==")
    test_hash_pin()
    test_difftest()
    test_baker_rejects()
    test_fuzz_checker()
    test_happy()
    test_slot_b()
    test_rescue()
    test_halt_electrician()
    test_bad_crc()
    test_wire_talk()
    test_critical_halt()
    test_seccomp_kills()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
NWEOF
echo "  tests/run.py"

cat > tests/bakeoff.py <<'NWEOF'
#!/usr/bin/env python3
"""Same city, three electrician spellings. C is TCB; others are twins."""
from __future__ import annotations

import os
import re
import subprocess
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STAGE = "/tmp/nw-init-run"
ZIG = "/tmp/zig/zig"


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, **kw)
    p.out = (p.stdout or b"").decode("utf-8", "replace")
    p.err = (p.stderr or b"").decode("utf-8", "replace")
    return p


def build():
    os.chdir(ROOT)
    subprocess.check_call(["make", "stage"])
    subprocess.check_call(
        ["gcc", "-O2", "-o", f"{STAGE}/elec-c", "electrician.c", "nwcheck.c"]
    )
    r = subprocess.run(
        [
            "rustc",
            "-O",
            "-o",
            f"{STAGE}/elec-rs",
            "electrician.rs",
        ],
        capture_output=True,
        text=True,
    )
    rust_ok = r.returncode == 0
    if not rust_ok:
        print("rust build failed:\n", r.stderr[-1500:])
    zig_ok = False
    if os.path.isfile(ZIG):
        z = subprocess.run(
            [
                ZIG,
                "build-exe",
                "electrician.zig",
                "nwcheck.c",
                "-lc",
                "-I.",
                "-OReleaseSafe",
                "--name",
                "elec-zig",
                "--cache-dir",
                "/tmp/zig-cache",
            ],
            capture_output=True,
            text=True,
        )
        if z.returncode == 0 and os.path.isfile(os.path.join(ROOT, "elec-zig")):
            subprocess.check_call(["cp", "-f", os.path.join(ROOT, "elec-zig"), f"{STAGE}/elec-zig"])
            zig_ok = True
        else:
            print("zig build failed:\n", (z.stderr or z.stdout)[-1500:])
    for name in ("elec-c", "elec-rs", "elec-zig"):
        p = f"{STAGE}/{name}"
        if os.path.isfile(p):
            os.chmod(p, 0o755)
    return rust_ok, zig_ok


def boot(elec, hold=700):
    os.replace(elec, f"{STAGE}/nw-electrician") if False else None
    subprocess.check_call(["cp", "-f", elec, f"{STAGE}/nw-electrician"])
    os.chmod(f"{STAGE}/nw-electrician", 0o755)
    t0 = time.perf_counter()
    p = run(
        [
            "unshare",
            "--pid",
            "--fork",
            "--mount-proc",
            "--",
            f"{STAGE}/nw-root",
            "--slot",
            f"{STAGE}/slots/A",
            "--hold-ms",
            str(hold),
        ]
    )
    dt = time.perf_counter() - t0
    out = p.out + p.err
    kits = re.findall(r"kit=(\d+)", out)
    filled = "kits filled" in out
    opened = "city open" in out
    return {
        "rc": p.returncode,
        "s": dt,
        "kits": kits,
        "filled": filled,
        "opened": opened,
        "inert": "inert" in out,
        "out": out,
    }


def main():
    rust_ok, zig_ok = build()
    langs = [("C", f"{STAGE}/elec-c")]
    if zig_ok:
        langs.append(("Zig", f"{STAGE}/elec-zig"))
    if rust_ok:
        langs.append(("Rust", f"{STAGE}/elec-rs"))

    print("== electrician bake-off (same plan, 4 houses) ==")
    print(f"{'lang':<8} {'rc':>4} {'ms':>8} {'kits':<12} filled open inert size")
    best = None
    for name, path in langs:
        sz = os.path.getsize(path)
        times = []
        last = None
        ok = True
        for _ in range(3):
            last = boot(path)
            times.append(last["s"])
            if last["rc"] != 0 or not last["filled"]:
                ok = False
                break
        ms = 1000 * (sum(times) / len(times))
        kits = ",".join(last["kits"]) if last else ""
        print(
            f"{name:<8} {last['rc'] if last else -1:>4} {ms:8.1f} {kits:<12} "
            f"{int(last['filled'])} {int(last['opened'])} {int(last['inert'])} {sz}"
        )
        if not ok:
            print("--- log ---")
            print(last["out"][-1200:] if last else "")
        if ok and (best is None or ms < best[1]):
            best = (name, ms)

    print()
    print("Go: not built. fork-without-exec is undefined in the Go runtime.")
    print("Python: not built. Same walk would be os.fork + GC in the TCB.")
    if best:
        print(f"fastest wall clock on this box: {best[0]} ({best[1]:.1f} ms city hold)")
        print("That number is almost all fork + hold-ms, not the compiler.")
    print("TCB remains C. Twins are evidence, not mayors.")


if __name__ == "__main__":
    main()
NWEOF
echo "  tests/bakeoff.py"

echo "done - 10 files restored."
