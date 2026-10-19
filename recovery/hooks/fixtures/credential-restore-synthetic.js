// Test-owned synthetic restore flow; no copied native module or agent data.
var WidgetResponses = class {
  async restoreCredentialRequestExpiryTimers() {
    const agentIds = await this.tm.sessionStore.listAgentRecordIds();
    for (const agentId of agentIds) {
      const liveSession = this.tm.liveSessions.get(agentId);
      let session;
      try {
        session = liveSession ?? await this.tm.sessionStore.openSession(agentId);
        for (const item of session.getTranscript()) {
          if (item?.kind !== "send-message" || item.message.type !== "credential-request" ||
              item.credentialResolution != null) continue;
          if (item.message.expiresAt <= this.now) this.expire(agentId, item.id);
          else if (item.message.brokered) this.restoreBrokered(agentId, item.id);
          else this.scheduleTimer(agentId, item.id);
        }
      } catch (problem) {
        this.reportFailure(agentId, problem);
      } finally {
        if (session != null && liveSession == null) session.dispose();
      }
    }
  }
  cancelCredentialRequestExpiry(requestId) { return requestId; }
};
