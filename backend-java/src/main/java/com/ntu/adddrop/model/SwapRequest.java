package com.ntu.adddrop.model;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Size;

// FastAPI's /api/submit-swap API endpoint
public class SwapRequest {
    
    private int numModules;

    @NotEmpty(message = "At least one module is required")
    private List<ModuleData> modules;

    // Nested class for module swap data
    public static class ModuleData {
        @NotBlank(message = "Old index is required")
        @Size(max = 10, message = "Old index must not exceed 10 characters")
        private String oldIndex;

        @NotBlank(message = "New indexes are required")
        private String newIndexes; // comma-separated string for multiple indexes

        // Constructors
        public ModuleData() {}

        public ModuleData(String oldIndex, String newIndexes) {
            this.oldIndex = oldIndex;
            this.newIndexes = newIndexes;
        }

        // Getters and Setters
        public String getOldIndex() {
            return oldIndex;
        }

        public void setOldIndex(String oldIndex) {
            this.oldIndex = oldIndex;
        }

        public String getNewIndexes() {
            return newIndexes;
        }

        public void setNewIndexes(String newIndexes) {
            this.newIndexes = newIndexes;
        }

        // Helper method to parse comma-separated new indexes
        public List<String> getNewIndexesList() {
            return List.of(newIndexes.split(","))
                        .stream()
                        .map(String::trim)
                        .filter(s -> !s.isEmpty())
                        .toList();
        }

        @Override
        public String toString() {
            return "ModuleData{oldIndex='" + oldIndex + "', newIndexes='" + newIndexes + "'}";
        }
    }

    // Constructors
    public SwapRequest() {}

    public SwapRequest(int numModules, List<ModuleData> modules) {
        this.numModules = numModules;
        this.modules = modules;
    }

    // Getters and Setters
    public int getNumModules() {
        return numModules;
    }

    public void setNumModules(int numModules) {
        this.numModules = numModules;
    }

    public List<ModuleData> getModules() {
        return modules;
    }

    public void setModules(List<ModuleData> modules) {
        this.modules = modules;
    }

    @Override
    public String toString() {
        return "SwapRequest{numModules=" + numModules + ", modules=" + modules + "}";
    }
}
