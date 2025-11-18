package com.ntu.adddrop.controller;

import com.ntu.adddrop.model.SessionData;
import com.ntu.adddrop.service.SessionService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api")
@CrossOrigin(origins = {
    "http://localhost:3000", 
    "https://ntu-add-drop-automator.vercel.app",
    "https://ntu-add-drop-automator*.vercel.app"
})
public class SwapController {
    
    @Autowired
    private SessionService sessionService;

    /* Submit swap endpoint: /api/submit-swap */
    @PostMapping("/submit-swap")
    public ResponseEntity<Map<String, Object>> submitSwap(
        @RequestBody Map<String, Object> swapData,
        HttpServletRequest request) {
        
        try {
            // Get session ID from HTTP session
            String sessionId = (String) request.getSession().getAttribute("sessionId");
            if (sessionId == null) {
                return ResponseEntity.status(401).body(Map.of(
                    "success", false,
                    "message", "No active session found"
                ));
            }

            // Extract module data from request
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> moduleDataList = (List<Map<String, Object>>) swapData.get("modules");

            if (moduleDataList == null || moduleDataList.isEmpty()) {
                return ResponseEntity.status(400).body(Map.of(
                    "success", false,
                    "message", "No module data provided"
                ));
            }

            // Convert to SessionData.ModuleStatus objects directly from Form
            List<SessionData.ModuleStatus> modules = new ArrayList<>();
            for (Map<String, Object> moduleMap : moduleDataList) {
                // Extract and process data first
                String oldIndex = (String) moduleMap.get("oldIndex");
                List<String> newIndexes = processNewIndexes(moduleMap.get("new_indexes"));
                
                // Create object with parameterized constructor (faster & cleaner)
                SessionData.ModuleStatus moduleStatus = new SessionData.ModuleStatus(
                    oldIndex, 
                    newIndexes, 
                    false, 
                    "Pending"
                );
                
                modules.add(moduleStatus);
            }

            // Update session with module data and start swap
            SessionData updates = new SessionData();
            updates.setModules(modules);
            updates.setSwapStatus("Processing");
            updates.setSwapMessage("Swap request submitted, starting automation...");
            updates.setSwapStartedAt(System.currentTimeMillis() / 1000);

            sessionService.updateSessionData(sessionId, updates);

            // TODO: Start async Selenium automation (Phase 3)
            // For now, just return success

            return ResponseEntity.ok(Map.of(
                "success", true,
                "message", "Swap request submitted successfully",
                "session_id", sessionId
            ));
        } catch (SecurityException e) {
            return ResponseEntity.status(401).body(Map.of(
                "success", false,
                "message", e.getMessage()
            ));
        } catch (Exception e) {
            return ResponseEntity.status(500).body(Map.of(
                "success", false,
                "message", "Failed to submit swap request: " + e.getMessage()
            ));
        }
    }

    // Helper method for cleaner code organization
    private List<String> processNewIndexes(Object newIndexesObj) {
        if (newIndexesObj instanceof String) {
            String newIndexesStr = (String) newIndexesObj;
            return List.of(newIndexesStr.split(","))
                    .stream()
                    .map(String::trim)
                    .filter(s -> !s.isEmpty())
                    .toList();
        } else {
            @SuppressWarnings("unchecked")
            List<String> indexList = (List<String>) newIndexesObj;
            return indexList;
        }
    }

    /* Get Swap Status endpoint: /api/swap-status/{sessionId} */
    @GetMapping("/swap-status/{sessionId}")
    public ResponseEntity<Map<String, Object>> getSwapStatus(@PathVariable String sessionId) {
        try {
            SessionData sessionData = sessionService.getSecureSession(sessionId);

            return ResponseEntity.ok(Map.of(
                "success", sessionData.getSwapStatus() != null ? sessionData.getSwapStatus() : "Idle",
                "message", sessionData.getSwapMessage() != null ? sessionData.getSwapMessage() : "",
                "started_at", sessionData.getSwapStartedAt(),
                "details", sessionData.getModules() != null ? sessionData.getModules() : new ArrayList<>()
            ));
        } catch (SecurityException e) {
            return ResponseEntity.status(401).body(Map.of(
                "status", "error",
                "message", e.getMessage()
            ));
        } catch (Exception e) {
            return ResponseEntity.status(500).body(Map.of(
                "status", "Error",
                "message", "Failed to get swap status: " + e.getMessage()
            ));
        }
    }

    /* Stop swap endpoint: /api/stop-swap/{sessionId} */
    @PostMapping("/stop-swap/{sessionId}")
    public ResponseEntity<Map<String, Object>> stopSwap(@PathVariable String sessionId) {
        try {
            // Update session to stopped status
            sessionService.updateOverallSwapStatus(sessionId, "Stopped", "Swap process stopped by user");

            // TODO: Stop async Selenium process (Phase 3)

            return ResponseEntity.ok(Map.of(
                "success", true,
                "message", "Swap process stopped successfully"
            ));
        } catch (SecurityException e) {
            return ResponseEntity.status(401).body(Map.of(
                "success", false,
                "message", e.getMessage()
            ));
        } catch (Exception e) {
            return ResponseEntity.status(500).body(Map.of(
                "success", false,
                "message", "Failed to stop swap process: " + e.getMessage()
            ));
        }
    }
}
