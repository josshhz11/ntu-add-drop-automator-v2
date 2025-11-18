package com.ntu.adddrop.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.ntu.adddrop.model.SessionData;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.stereotype.Service;

import java.security.SecureRandom;
import java.time.Duration;
import java.util.Base64;

@Service
public class SessionService {
    
    @Autowired
    private RedisTemplate<String, String> redisTemplate;

    @Autowired
    private ObjectMapper objectMapper;

    private static final Duration SESSION_TTL = Duration.ofHours(2);
    private static final SecureRandom secureRandom = new SecureRandom();

    // FastAPI's create_secure_session function
    public String createSecureSession(String username, String encryptedPassword) {
        String sessionId = generateSessionId();

        SessionData sessionData = new SessionData();
        sessionData.setUsername(username);
        sessionData.setEncryptedPassword(encryptedPassword);
        sessionData.setAuthenticated(true);
        sessionData.setCreatedAt(System.currentTimeMillis() / 1000);
        sessionData.setCreatedAt(System.currentTimeMillis() / 1000 + 7200);
        sessionData.setSwapStatus("Idle");
        sessionData.setSwapMessage(null);
        sessionData.setSwapStartedAt(null);

        try {
            String sessionJson = objectMapper.writeValueAsString(sessionData);
            redisTemplate.opsForValue().set(
                "session:" + sessionId,
                sessionJson,
                SESSION_TTL
            );
        } catch (Exception e) {
            throw new RuntimeException("Failed to create session", e);
        }

        return sessionId;
    }

    public SessionData getSecureSession(String sessionId) {
        try {
            String sessionJson = redisTemplate.opsForValue().get("session:" + sessionId);
            if (sessionJson == null) {
                throw new SecurityException("Session expired or invalid");
            }

            SessionData sessionData = objectMapper.readValue(sessionJson, SessionData.class);

            if (System.currentTimeMillis() / 1000 > sessionData.getExpiresAt()) {
                redisTemplate.delete("session:" + sessionId);
                throw new SecurityException("Session expired");
            }

            return sessionData;
        } catch (Exception e) {
            if (e instanceof SecurityException) {
                throw new SecurityException("Error: ", e);
            }
            throw new RuntimeException("Failed to retrieve session", e);
        }
    }

    public void updateSessionData(String sessionId, SessionData updates) {
        try {
            SessionData sessionData = getSecureSession(sessionId);

            if (updates.getSwapStatus() != null) {
                sessionData.setSwapStatus(updates.getSwapStatus());
            }
            if (updates.getSwapMessage() != null) {
                sessionData.setSwapMessage(updates.getSwapMessage());
            }
            if (updates.getSwapStartedAt() != null) {
                sessionData.setSwapStartedAt(updates.getSwapStartedAt());
            }
            if (updates.getModules() != null) {
                sessionData.setModules(updates.getModules());
            }

            String sessionJson = objectMapper.writeValueAsString(sessionData);

            Long ttl = redisTemplate.getExpire("session:" + sessionId);
            if (ttl != null && ttl > 0) {
                redisTemplate.opsForValue().set(
                    "session:" + sessionId,
                    sessionJson,
                    Duration.ofSeconds(ttl)
                );
            }
        } catch (Exception e) {
            throw new RuntimeException("Failed to update session", e);
        }
    }

    public void updateOverallSwapStatus(String sessionId, String status, String message) {
        SessionData updates = new SessionData();
        updates.setSwapStatus(status);
        updates.setSwapMessage(message);
        updateSessionData(sessionId, updates);
    }

    public void cleanupSession(String sessionId) {
        redisTemplate.delete("session:" + sessionId);
    }

    private String generateSessionId() {
        byte[] bytes = new byte[32];
        secureRandom.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }
}
