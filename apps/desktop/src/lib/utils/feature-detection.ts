export function isFileSystemAccessApiSupported() {
    return 'showDirectoryPicker' in window;
}
