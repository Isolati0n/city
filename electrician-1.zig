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
