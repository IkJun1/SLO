let moduleDeps = {
    baseName: (path) => String(path || ''),
    clearEditor: () => {},
    expandAncestors: () => {},
    loadDoc: async () => {},
    loadImagePreview: async () => {},
    refreshEditorFilename: () => {},
    refreshFileTree: async () => {},
    renameDocWithPath: async () => ({}),
    renameFolderWithPath: async () => ({}),
    renameImageWithPath: async () => ({}),
    remapMovedPath: (path) => path,
    setSelectedEntry: () => {},
    showSelectedStatus: () => {}
};

export function configureMarkdownTreeModule(deps) {
    moduleDeps = {
        ...moduleDeps,
        ...deps
    };
}

export function getMarkdownTreeModuleDeps() {
    return moduleDeps;
}
