use serde::Serialize;
use std::{
    io::{self, Read},
    process::{Child, Command, ExitStatus, Stdio},
    sync::mpsc::{self, Receiver, TryRecvError},
    thread,
    time::{Duration, Instant},
};

pub const STREAM_LIMIT: usize = 32 * 1024;
pub const CLEANUP_SCOPE: &str = "direct_child_only_descendants_not_tracked";
const POLL: Duration = Duration::from_millis(10);
const REAP_GRACE: Duration = Duration::from_secs(1);

#[derive(Serialize)]
pub struct StreamReport {
    bytes_observed: usize,
    limit_exceeded: bool,
    read_failed: bool,
}

struct Stream {
    receiver: Receiver<StreamReport>,
    report: Option<StreamReport>,
}

impl Stream {
    fn start(mut reader: impl Read + Send + 'static) -> io::Result<Self> {
        let (sender, receiver) = mpsc::channel();
        thread::Builder::new().spawn(move || {
            let mut bytes_observed = 0;
            let mut read_failed = false;
            let mut buffer = [0; 4096];
            while bytes_observed <= STREAM_LIMIT {
                let remaining = (STREAM_LIMIT + 1 - bytes_observed).min(buffer.len());
                match reader.read(&mut buffer[..remaining]) {
                    Ok(0) => break,
                    Ok(count) => bytes_observed += count,
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                    Err(_) => {
                        read_failed = true;
                        break;
                    }
                }
            }
            let _ = sender.send(StreamReport {
                bytes_observed,
                limit_exceeded: bytes_observed > STREAM_LIMIT,
                read_failed,
            });
        })?;
        Ok(Self {
            receiver,
            report: None,
        })
    }

    fn poll(&mut self) {
        if self.report.is_none() {
            self.report = match self.receiver.try_recv() {
                Ok(report) => Some(report),
                Err(TryRecvError::Empty) => None,
                Err(TryRecvError::Disconnected) => Some(StreamReport {
                    bytes_observed: 0,
                    limit_exceeded: false,
                    read_failed: true,
                }),
            };
        }
    }
}

#[derive(Serialize)]
pub struct Outcome {
    pub pid: u32,
    pub exit_code: Option<i32>,
    #[cfg(unix)]
    signal: Option<i32>,
    pub exit_success: bool,
    pub issue: Option<&'static str>,
    elapsed_ms: u64,
    pub stdout: Option<StreamReport>,
    pub stderr: Option<StreamReport>,
    cleanup_scope: &'static str,
    kill_requested: bool,
    kill_request_succeeded: bool,
    direct_child_reaped: bool,
}

pub fn run(
    command: &mut Command,
    timeout: Duration,
    on_start: impl FnOnce(u32) -> io::Result<()>,
) -> io::Result<Outcome> {
    let started = Instant::now();
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let pid = child.id();
    let logged = on_start(pid);
    let stdout = Stream::start(child.stdout.take().expect("stdout configured as pipe"));
    let stderr = Stream::start(child.stderr.take().expect("stderr configured as pipe"));
    let mut issue = if logged.is_err() {
        Some("event_write_failed")
    } else if stdout.is_err() || stderr.is_err() {
        Some("capture_start_failed")
    } else {
        None
    };
    let mut stdout = stdout.ok();
    let mut stderr = stderr.ok();
    let mut status = None;

    while issue.is_none() {
        if status.is_none() {
            match child.try_wait() {
                Ok(observed) => status = observed,
                Err(_) => issue = Some("wait_failed"),
            }
        }
        let reports = [&mut stdout, &mut stderr].map(|stream| {
            stream.as_mut().and_then(|stream| {
                stream.poll();
                stream.report.as_ref()
            })
        });
        if reports.iter().flatten().any(|report| report.limit_exceeded) {
            issue = Some("output_limit_exceeded");
        } else if reports.iter().flatten().any(|report| report.read_failed) {
            issue = Some("capture_read_failed");
        } else if started.elapsed() >= timeout {
            issue = Some("timed_out");
        } else if status.is_some() && reports.iter().all(Option::is_some) {
            break;
        }
        if issue.is_none() {
            thread::sleep(POLL.min(timeout.saturating_sub(started.elapsed())));
        }
    }

    let kill_requested = status.is_none();
    let kill_request_succeeded = kill_requested && child.kill().is_ok();
    if kill_requested {
        status = try_reap(&mut child);
    }
    let [stdout, stderr] = [stdout, stderr].map(|stream| {
        stream.and_then(|mut stream| {
            stream.poll();
            stream.report
        })
    });

    #[cfg(unix)]
    use std::os::unix::process::ExitStatusExt;
    Ok(Outcome {
        pid,
        exit_code: status.and_then(|status| status.code()),
        #[cfg(unix)]
        signal: status.and_then(|status| status.signal()),
        exit_success: status.is_some_and(|status| status.success()),
        issue,
        elapsed_ms: started.elapsed().as_millis() as u64,
        stdout,
        stderr,
        cleanup_scope: CLEANUP_SCOPE,
        kill_requested,
        kill_request_succeeded,
        direct_child_reaped: status.is_some(),
    })
}

fn try_reap(child: &mut Child) -> Option<ExitStatus> {
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Some(status),
            Err(_) => return None,
            Ok(None) if started.elapsed() >= REAP_GRACE => return None,
            Ok(None) => thread::sleep(POLL),
        }
    }
}
