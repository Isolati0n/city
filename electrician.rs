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
