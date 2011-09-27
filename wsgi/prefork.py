#!/usr/bin/python
#
# A preforking server framework, modeled after how I have read the Apache
# approach described.
#
# There is a master dispatcher and a number of workers. The master does
# a select() on the server socket; when it becomes ready, the master picks
# an idle worker and commands it to do an accept() and handle the request.
# The worker tells the master either that it is now busy (and later that
# it is idle again) or that for some reason the accept() has failed.
#
# If there are no idle workers and we are under the maximum worker
# count, we start a new worker process (and have it handle the
# request). If we are already at the maximum, the master accept()s and
# immediately discards the new connection.
#
# Workers communicate with the master through a socketpair (faked with
# pipes to accomodate pre 2.4 versions of Python), and normally spend
# their time in a read() of the socketpair. The master select()s on the
# listening socket and all of its ends of the socketpairs.
#
# When the master commands a worker to do an accept(), it must wait
# for the worker to respond one way or another. The master shoots
# worker processes that don't respond fast enough.

__doc__ = """
A generic framework for preforking network servers, where a master
process supervises a pool of forked worker processes that handle
the actual connections. Worker processes are started as necessary,
up to a maximum number.

To create a preforking server pool, instantiate an instance of
the ServerPool class with appropriate parameters and call its
.serve() method.
"""
__all__ = ['ServerPool', 'ServerShutdown', 'CleanShutdown']

import os, socket, select
import time

import sys
def debug(msg):
	pid = os.getpid()
	sys.stderr.write("%d: %s\n" % (pid, msg))
	sys.stderr.flush()

# We happen to know that we only select for readability.
# Our timeout handling is a hack, too.
# Life is just better if we have a real socketpair().
class PipeSPair(object):
	"""A limited emulation of one end of a socketpair() with pipes."""
	def __init__(self, sfd, rfd):
		self.sfd = sfd
		self.rfd = rfd
		self.tmo = None
	def fileno(self):
		return self.rfd
	def recv(self, blen):
		seltup = select.select([self.rfd,], [], [], self.tmo)
		if not seltup[0]:
			raise socket.error("PipeSPair read timeout")
		return os.read(self.rfd, blen)
	def send(self, buf):
		seltup = select.select([], [self.sfd,], [], self.tmo)
		if not seltup[1]:
			raise socket.error("PipeSPair send timeout")
		return os.write(self.sfd, buf)
	def settimeout(self, tmo):
		self.tmo = tmo
	def __str__(self):
		return '<PipeSPair %d: sfd %d rfd %d>' % (id(self), self.sfd, self.rfd)
	# We need an explicit del because we have to close our pipe
	# file descriptors.
	def __del__(self):
		for fd in (self.sfd, self.rfd):
			try:
				os.close(fd)
			except EnvironmentError:
				pass

def get_pair():
	"""Half-assedly emulate socketpair() with pipes."""
	(cin, sout) = os.pipe()
	(sin, cout) = os.pipe()
	srv = PipeSPair(sout, sin)
	clnt = PipeSPair(cout, cin)
	return (srv, clnt)

class ServerShutdown(Exception):
	"""Raised by the connection processing function to signal that
	the server should close down the worker pool and exit."""
class CleanShutdown(Exception):
	"""Raised by the connection processing function to signal that
	the server should cleanly close down the worker pool and exit.
	This is only really possible with Unix domain sockets."""

# Sent by the master: ACCEPT means 'do an accept()'; EXIT means 'exit'.
#
# Sent by the workers:
# DYING means 'I am going away'
# SKIPPED means 'the accept() errored out'
# BUSY means 'I accept()'d and am now processing'
# IDLE means 'I am idle and ready for more'
# SHUTDOWN means 'please shut down the entire show'
#
# (SKIPPED is distinct from IDLE so that we can notice synchronization
# errors faster.)
#
# Workers signal that they have come up by sending an initial IDLE.
#
ACCEPT = 'a'
EXIT = 'e'
DYING = 'd'
SKIPPED = 's'
BUSY = 'b'
IDLE = 'i'
SHUTDOWN = 'S'
CLEANDOWN= 'D'
# the values returned by ServerPool.cycle().
Timeout, Processed, Overload = range(0, 3)
class ServerPool(object):
	"""ServerPool(ssock, proc_func, wmin, wmax, rqmax) -> pool object

	Create a new preforking server pool to handle new connections
	on ssock, calling proc_func (from a worker process) for each
	new connection. After a pool is created, serving is started
	by calling pool.serve().

	proc_func is called with a single argument, the socket for the
	new connection. If it raises a ServerShutdown or CleanShutdown
	exception, the entire server pool is shut down in an orderly
	way.

	wmin is the minimum number of worker processes to have.
	wmax is the maximum number of worker processes.
	rqmax is the maximum number of requests handled by a single
	worker process before it exits and restarts; zero means that
	there is no limit.
	By default the pool attempts to keep one spare idle worker
	process around. This can be changed by calling .set_min_idle().

	If connections are received when there are already wmax busy
	worker processes, they are accepted and then immediately dropped.
	(Although this can be changed by calling .stall_on_overload().)"""
	
	def __init__(self, ssock, proc_func, wmin, wmax, rqmax):
		self.wmin = wmin
		self.wmax = wmax
		self.rqmax = rqmax
		self.ssock = ssock
		self.prfunc = proc_func
		self.ovstall = False
		self.min_idle = 1
		self.running = True
		self.downing = False
		# kids maps (server) channel ends to PIDs.
		# kidpids maps PIDs to server channel ends.
		# idle_kids lists channel ends of idle kids.
		self.kids = {}
		self.kidpids = {}
		self.idle_kids = []
		self.pending_kids = []
		self.ctimes = {}

	def stall_on_overload(self, flag):
		"""Set the behavior on overload. FLAG is True if new
		connections should be stalled, False if they should be
		discarded. The default is False."""
		self.ovstall = flag

	def set_min_idle(self, num):
		"""Set the minimum number of idle worker processes that the
		pool will try to keep available to NUM. The default is 1."""
		if num < 0:
			num = 0
		self.min_idle = num
		
	def _accept(self):
		"""accept() on the server socket, returning either a new
		(blocking) socket or None."""
		try:
			(nsock, raddr) = self.ssock.accept()
			nsock.setblocking(1)
			return nsock
		except (EnvironmentError, socket.error):
			return None

	# The worker processing function. We deal with all network
	# errors by bailing out.
	def worker(self, cmdchan):
		"""A worker process listening for instructions on CMDCHAN."""
		try:
			handled = 0
			stop = None
			cmdchan.send(IDLE)
			while 1:
				r = cmdchan.recv(1)
				if r != ACCEPT:
					cmdchan.send(DYING)
					return
				nsock = self._accept()
				if not nsock:
					cmdchan.send(SKIPPED)
					continue
				cmdchan.send(BUSY)
				try:
					self.prfunc(nsock)
				except ServerShutdown:
					stop = SHUTDOWN
				except CleanShutdown:
					stop = CLEANDOWN
				del nsock
				handled += 1
				if stop:
					cmdchan.send(stop)
				elif self.rqmax and handled > self.rqmax:
					cmdchan.send(DYING)
					return
				else:
					cmdchan.send(IDLE)
		except (EnvironmentError, socket.error, select.error):
			return

	def start_worker(self, async=False):
		"""Create a new worker process and wait for it to become
		ready (unless the optional async argument is True).
		The new worker is in self.idle_kids."""
		(srv, clnt) = get_pair()
 		pid = os.fork()
		if not pid:
			# In the new worker.
			# del srv confuses pychecker's mind.
			srv = None
			self.worker(clnt)
			os._exit(0)
			return None
		else:
			# Still in the master. Register the new
			# worker, and then if we're not async wait
			# for it to tell us it's come up, and add it
			# to the idle pool
			clnt = None
			self.kids[srv] = pid
			self.kidpids[pid] = srv
			self.ctimes[srv] = time.time()
			# Limit how long we will wait for workers to
			# ack us.
			srv.settimeout(1)
			if async:
				# add async kids to the list of kids
				# pending activation.
				self.pending_kids.append(srv)
			else:
				# wait for the initial startup note.
				# if it fails, shoot the kid.
				r = self.recv_resp(srv)
				if r == IDLE:
					self.idle_kids.append(srv)
				else:
					self.shoot_kid(srv)

	def drop_kid(self, chan):
		"""Remove worker CHAN from our data structures."""
		if chan not in self.kids:
			return
		if chan in self.idle_kids:
			self.idle_kids.remove(chan)
		if chan in self.pending_kids:
			self.pending_kids.remove(chan)
		pid = self.kids[chan]
		del self.kidpids[pid]
		del self.kids[chan]
		del self.ctimes[chan]

	def reap_kids(self):
		"""Wait for any dead processes, cleaning them out of our
		data structures as necessary."""
		while 1:
			try:
				(pid, stat) = os.waitpid(-1, os.WNOHANG)
			except EnvironmentError:
				return
			if pid == 0:
				return
			if pid not in self.kidpids:
				continue
			srv = self.kidpids[pid]
			self.drop_kid(srv)

	def shoot_kid(self, chan):
		"""Forcefully terminate and remove worker CHAN."""
		# At this point we know something has gone wrong, so we
		# might as well check for general cleanup.
		self.reap_kids()
		if chan not in self.kids:
			return
		pid = self.kids[chan]
		os.kill(pid, 9)
		self.drop_kid(chan)
		self.reap_kids()

	def send_cmd(self, chan, cmd):
		"""Send CMD to worker CHAN. Terminate CHAN on errors."""
		try:
			chan.send(cmd)
			return True
		except (EnvironmentError, socket.error, select.error):
			self.shoot_kid(chan)
			return False
	def recv_resp(self, chan):
		"""Receive a response from worker CHAN. Terminate CHAN on
		errors."""
		try:
			r = chan.recv(1)
			return r
		except (EnvironmentError, socket.error, select.error):
			self.shoot_kid(chan)
			return None

	def sort_idle(self):
		"""Sort idle workers into creation order."""
		def _ctime_cmp(a, b):
			return cmp(self.ctimes[a], self.ctimes[b])
		self.idle_kids.sort(_ctime_cmp)

	def insure_min_kids(self):
		"""Bring up enough new worker threads to meet our
		targets for the minimum number of workers and idle
		workers."""
		# Do not (re)start workers if we are not running.
		if not self.running:
			return
		while len(self.kids) < self.wmin:
			self.start_worker(True)
		if not self.min_idle:
			return

		# Start however many new workers we can, up to either
		# a) the number of maximum workers or b) the minimum
		# number of idle workers.
		nrem = self.wmax - len(self.kids)
		cidl = len(self.idle_kids) + len(self.pending_kids)
		tnum = min(nrem, self.min_idle - cidl)
		if tnum <= 0:
			return
		for _ in range(0, tnum):
			self.start_worker(True)

	def dispatch(self):
		"""Attempt to dispatch a new connection to a worker process.
		Returns True if it succeeded in doing so and False if the new
		connection is still pending."""
		# try to activate a pending kid if necessary and possible.
		while not self.idle_kids and self.pending_kids:
			chan = self.pending_kids.pop(0)
			r = self.recv_resp(chan)
			if r == IDLE:
				self.idle_kids.append(chan)
			else:
				self.shoot_kid(chan)

		# at this point we axiomatically have no pending kids;
		# one way or another, they have been cleared above.
		if not self.idle_kids and \
		   len(self.kids) < self.wmax:
			# This is a synchronous start because we need
			# it *right now*.
			self.start_worker()

		# We try to always pick the oldest kid.
		# This has two effects: a) one kid stays hot, and b)
		# except under high load, the next kid will not also
		# immediately expire (the way it would if we did a
		# round-robin).
		self.sort_idle()
		while self.idle_kids:
			chan = self.idle_kids.pop(0)
			assert (chan in self.kids)
			if not self.send_cmd(chan, ACCEPT):
				continue
			r = self.recv_resp(chan)
			if r is None:
				# our best guess is that this was
				# handled? May be false...
				continue
			
			if r == SKIPPED:
				self.idle_kids.append(chan)
			elif r != BUSY:
				self.shoot_kid(chan)

			self.insure_min_kids()
			return True

		# Did not handle, return false.
		self.insure_min_kids()
		return False

	# Must be called with the channel ready to receive.
	def ack_kid(self, chan):
		"""Handle the ready worker channel CHAN."""
		if chan not in self.kids:
			return
		r = self.recv_resp(chan)
		if r is None:
			return
		if r == IDLE:
			assert(chan not in self.idle_kids)
			self.idle_kids.append(chan)
			if chan in self.pending_kids:
				self.pending_kids.remove(chan)
		elif r == DYING:
			self.drop_kid(chan)
			self.insure_min_kids()
		elif r in (SHUTDOWN, CLEANDOWN):
			# This worker *is* idle now...
			self.idle_kids.append(chan)
			# We may get multiple shutdown requests; we should
			# ignore this as much as possible. We use a separate
			# variable from self.running because shutdown_accept()
			# does not immediately turn off self.running.
			if not self.downing:
				self.downing = True
				if r == CLEANDOWN:
					self.shutdown_accept()
				self.shutdown()
		else:
			self.shoot_kid(chan)

	# Possible results: timer expired without anything happening;
	# things processed; server socket overloaded.
	def cycle(self, doaccept, timeout):
		"""Perform one cycle of the master processing loop.
		Returns Timeout, Processed, or Overload depending on
		what happened."""
		sockl = self.kids.keys()
		if doaccept:
			sockl.append(self.ssock)
		# Handle a potential error condition that would otherwise
		# make us sleep forever.
		if not sockl:
			return Timeout
		try:
			seltup = select.select(sockl, [], [], timeout)
		except select.error:
			pass

		# Timeout expiry?
		if not seltup[0]:
			return Timeout

		res = Processed
		for rchan in seltup[0]:
			if rchan != self.ssock:
				self.ack_kid(rchan)

		# We must check self.running so that we do not restart
		# workers due to pending accepts after a shutdown().
		if self.running and self.ssock in seltup[0] and \
		   not self.dispatch():
			# Handle overload. If we can just flush overloaded
			# connections, we do that; otherwise we signal
			# overload to the higher levels so they can adjust
			# what they ask us to do.
			if self.ovstall:
				res = Overload
			else:
				nsock = self._accept()
				if nsock:
					nsock.close()
					del nsock
		return res

	def shutdown_accept(self):
		"""Clean up all pending connections. This only really makes
		sense when you are using Unix domain sockets and have removed
		the socket file, so that no new connections can arrive while
		we are in the middle of this."""
		# Normally we harvest pending connections by waiting
		# for them with a 0 timeout; when the timeout expires,
		# there are no pending connections and we're done. The
		# one complication is that if we are not flushing
		# people on overload and we overload, we must start
		# waiting for kids (only) with an infinite timeout.
		accept_on = True
		while 1:
			if accept_on:
				res = self.cycle(True, 0)
			else:
				res = self.cycle(False, None)
			if res == Timeout:
				break
			elif res == Overload:
				accept_on = False
			else:
				accept_on = True

	def shutdown_idle(self):
		"""Shut down all idle worker processes. Does not start any
		workers if we drop below our minimum number of workers."""
		# Get a copy of idle_kids, because we are about to mangle
		# it up.
		chans = self.idle_kids + []
		for chan in chans:
			if self.send_cmd(chan, EXIT):
				r = self.recv_resp(chan)
				if r == DYING:
					self.drop_kid(chan)
				else:
					self.shoot_kid(chan)
		self.reap_kids()

	def shutdown(self, timeout = 15):
		"""Shut down all worker processes in an orderly way. This
		must be called in the master process, not in a worker
		process."""
		# Flag that we are no longer running, and should neither
		# answer the server socket nor start new kids.
		self.running = False
		sttm = time.time()
		rem = timeout

		# We loop killing off idle kids and waiting for busy kids
		# to idle, until either there is nothing left or the timeout
		# expires.
		self.shutdown_idle()
		while self.kids and rem > 0:
			self.cycle(False, rem)
			self.shutdown_idle()
			rem = sttm + timeout - time.time()

		# If there are any remaining kids, the timeout has expired;
		# give up and kill them abruptly.
		chans = self.kids.keys()
		for chan in chans:
			self.shoot_kid(chan)

	def serve(self):
		"""Entry point to initiate serving."""
		for _ in range(0, self.wmin):
			self.start_worker()
		self.ssock.setblocking(0)
		accept_on = True
		while self.running:
			self.reap_kids()

			# If the server socket is stalled, check to see if
			# we can start listening to it again because we have
			# idle capacity.
			if not accept_on and \
			   (self.idle_kids or self.pending_kids or \
			    len(self.kids) < self.wmax):
				accept_on = True

			res = self.cycle(accept_on, None)
			if res == Overload:
				accept_on = False

#
# -----
# Basic testing functionality.
def process(nsock):
	pid = os.getpid()
	time.sleep(2)
	buf = nsock.recv(1024)
	print "proc %d: got %s" % (pid, buf)
	nsock.send("Thank you from %d\n" % pid)
	nsock.close()
	if buf == "shutdown\n":
		print "shutting down on command SIR!"
		raise CleanShutdown
	return

def main():
	s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	s.bind(('', 5000))
	s.listen(10)
	pool = ServerPool(s, process, 2, 10, 3)
	pool.stall_on_overload(True)
	#pool.set_min_idle(0)
	pool.serve()

if __name__ == "__main__":
	main()

#
# Copyright (C) 2007 Chris Siebenmann <cks+python@hawkwind.cs.toronto.edu>
#
# This program is free software. You can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
