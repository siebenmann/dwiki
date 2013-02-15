#
# Implement the systemd socket activation protocol, more or less.
# Parts of this are a hack because of Python limitations.
#
# The details of the systemd socket activation protocol are covered in
# http://0pointer.de/public/systemd-man/sd_listen_fds.html
# (in the 'Notes' section).
#
import socket, os, stat

# Listening FDs are passed starting at this file descriptor number.
# http://cgit.freedesktop.org/systemd/systemd/plain/src/systemd/sd-daemon.h
SD_LISTEN_FDS_START = 3

def sockinfo_from_fd(fd):
	"""Attempt to deduce the correct socket family and type for fd,
	which must be a socket file descriptor. This is a hack and may
	not work in all cases. Returns either (family, type) or None."""
	try:
		# socket internally dup()'s the file descriptor we give
		# it, so we don't need to do that.
		# AF_UNIX is the socket type that has the largest struct
		# sockaddr that is generally accessible, so we use it so
		# that getsockname() has the best chance of success.
		# Doing it the other way can fail in entertaining and
		# explosive ways.
		s = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
		stype = s.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)

		# This only works with BOUND sockets, but that's what we
		# get handed. Also, it can fail if we can't identify the
		# naming format.
		nm = s.getsockname()
		if isinstance(nm, str) and nm.startswith('/'):
			return (socket.AF_UNIX, stype)
		elif len(nm) == 2 and '.' in nm[0]:
			return (socket.AF_INET, stype)
		elif len(nm) == 4 and ':' in nm[0]:
			return (socket.AF_INET6, stype)
		else:
			return None
	except EnvironmentError:
		return None
	except socket.error:
		return None

def sd_listen_sockets(check_pid = True):
	"""Retrieve sockets passed to us by the systemd socket passing
	protocol. Works only for ordinary sockets, unlike the full protocol.
	check_pid is True if we should check $LISTEN_PID; if False we ignore
	it and look only for $LISTEN_FDS. Returns a possibly empty list of
	socket objects."""
	if 'LISTEN_FDS' not in os.environ:
		return []
	if check_pid:
		lpid = os.environ.get('LISTEN_PID', None)
		if not lpid:
			return []
		try:
			lpid = int(lpid, 10)
			if lpid != os.getpid():
				return []
		except ValueError:
			return []
	try:
		lcount = int(os.environ['LISTEN_FDS'], 10)
	except ValueError:
		return []

	r = []
	for i in range(lcount):
		try:
			fdn = SD_LISTEN_FDS_START + i
			# Is the FD actually valid and open?
			st = os.fstat(fdn)
			if not stat.S_ISSOCK(st.st_mode):
				continue
			sinfo = sockinfo_from_fd(fdn)
			if sinfo:
				sfam, styp = sinfo
			else:
				# we have to guess
				sfam = socket.AF_UNIX
				styp = socket.SOCK_STREAM
			s = socket.fromfd(fdn, sfam, styp)
			r.append(s)
			# socket has dup()'d the file descriptor, so we
			# must close it to avoid leaks.
			os.close(fdn)
			# We do not set FD_CLOEXEC. This is
			# deliberate.
		except EnvironmentError:
			pass
		except socket.error:
			pass
	return r

def sd_set_listen_sockets(slist):
	"""Set things up to pass a list of socket objects (or anything
	with a .fileno() method) via the systemd socket passing protocol
	to a command that we are going to exec(). This manipulates
	os.environ and assumes we are in a child that is about to exec()
	(so that the current PID is the correct value for $LISTEN_PID).
	This will destroy any unrelated open file descriptors from 3 up
	to 3+len(slist)-1."""
	smin = SD_LISTEN_FDS_START
	smax = smin + len(slist)
	os.environ['LISTEN_PID'] = "%d" % os.getpid()
	os.environ['LISTEN_FDS'] = '%d' % len(slist)
	fdarr = {}
	nlst = []
	for s in slist:
		sfd = s.fileno()
		if smin <= sfd < smax:
			fdarr[sfd] = True
		else:
			nlst.append(s)
	if not nlst:
		return
	# We must relocate nlst into smin-smax, avoiding used FDs.
	curfd = smin
	for s in nlst:
		while curfd in fdarr and curfd < smax:
			curfd += 1
		assert(curfd < smax)
		os.dup2(s.fileno(), curfd)
		fdarr[curfd] = True
		curfd += 1
	# Should be done.
	assert(not nlst)
