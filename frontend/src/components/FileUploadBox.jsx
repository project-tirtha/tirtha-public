import React, { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import Icon from './Icon';

function FileUploadBox({ selectedFiles = [], onFilesChange, allowOpen = true, onAuthRequired = null, accept = 'image/*,video/mp4,video/quicktime,.heic,.heif,.heics,.heifs,.mp4,.mov' }) {
  const { t } = useTranslation();
  const [dragActive, setDragActive] = useState(false);
  const inputRef = useRef(null);

  const handleFileSelect = (files) => {
    if (!allowOpen) {
      if (typeof onAuthRequired === 'function') onAuthRequired();
      return;
    }
    if (files && files.length > 0) {
      onFilesChange(Array.from(files));
    }
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    if (!allowOpen) {
      return;
    }
    setDragActive(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    setDragActive(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragActive(false);
    handleFileSelect(e.dataTransfer.files);
  };

  const fileCount = selectedFiles.length;
  const fileNames = selectedFiles
    .map((item) => (item?.name || item?.file?.name || ''))
    .filter(Boolean)
    .slice(0, 3)
    .join(', ');
  /*
    This is the box's own accessible text, and it was English with the plural
    spliced in by `fileCount > 1 ? 's' : ''` -- a rule no other language this
    site serves shares. i18next counts with the target language's own rules.
  */
  const fileLabel = fileCount
    ? t('contribute.filesSelectedNamed', {
        count: fileCount,
        names: `${fileNames}${fileCount > 3 ? '…' : ''}`,
      })
    : t('contribute.noFilesSelected');

  const handleOpenRequest = () => {
    if (!allowOpen) {
      if (typeof onAuthRequired === 'function') onAuthRequired();
      return;
    }
    inputRef.current?.click();
  };

  return (
    <div
      className={`file-upload-box ${dragActive ? 'drag-active' : ''}`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onClick={handleOpenRequest}
      role="button"
      tabIndex={0}
      /*
        Space as well as Enter. `role="button"` promises native button
        behaviour, and a native button fires on both -- so a keyboard user who
        pressed Space here got a scrolled page and no file picker, which is the
        only keyboard route to one. `preventDefault` stops that scroll.
      */
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
          e.preventDefault();
          handleOpenRequest();
        }
      }}
    >
        <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple
        style={{ display: 'none' }}
        /*
          Cleared after every pick, because a file input fires `change` only
          when its value actually changes. Leaving the last selection in it
          meant that removing a photograph from the list and choosing the same
          file again dispatched no event at all: no toast, no "already added",
          nothing -- the picker just closed and the box still read "No files
          selected". `handleFileSelect` has already copied the list out.
        */
        onChange={(e) => {
          handleFileSelect(e.target.files);
          e.target.value = '';
        }}
      />
      <Icon name="cloud_upload" className="upload-icon" />
      <div className="upload-text">
        <strong>{fileLabel}</strong>
        <p>{t('contribute.dropzone')}</p>
      </div>
    </div>
  );
}

export default FileUploadBox;
