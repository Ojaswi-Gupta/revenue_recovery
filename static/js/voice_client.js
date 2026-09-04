class VoiceClient {
    constructor(workflowId, chatContainerId, statusContainerId) {
        this.workflowId = workflowId;
        this.chatContainer = document.getElementById(chatContainerId);
        this.statusContainer = document.getElementById(statusContainerId);
        
        this.ws = null;
        this.mediaRecorder = null;
        this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        
        this.isRecording = false;
        this.silenceTimer = null;
        this.reconnectTimeout = null;
        
        this.initWebSocket();
    }
    
    initWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${protocol}//${window.location.host}/voice/ws/${this.workflowId}`);
        
        this.ws.onopen = () => {
            this.updateStatus('Connected', 'success');
            if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
        };
        
        this.ws.onmessage = async (event) => {
            if (event.data instanceof Blob) {
                this.playAudioData(event.data);
            } else {
                try {
                    const msg = JSON.parse(event.data);
                    this.handleMessage(msg);
                } catch (e) {
                    console.error('Invalid message format', e);
                }
            }
        };
        
        this.ws.onclose = () => {
            this.updateStatus('Disconnected', 'error');
            this.reconnectTimeout = setTimeout(() => this.initWebSocket(), 5000);
        };
        
        this.ws.onerror = (error) => {
            console.error('WebSocket Error:', error);
            this.updateStatus('Error', 'error');
        };
    }
    
    handleMessage(msg) {
        switch (msg.type) {
            case 'transcript':
                this.addChatBubble(msg.text, msg.sender === 'user' ? 'user' : 'ai');
                break;
            case 'status':
                this.updateStatus(msg.text, msg.level || 'info');
                break;
            case 'summary':
                this.addChatBubble(`Call Summary: ${msg.text}`, 'ai');
                break;
            default:
                console.log('Unknown message type:', msg);
        }
    }
    
    async startRecording() {
        if (this.isRecording) return;
        
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.mediaRecorder = new MediaRecorder(stream);
            const audioChunks = [];
            
            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    audioChunks.push(event.data);
                }
            };
            
            this.mediaRecorder.onstop = () => {
                const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                this.sendAudio(audioBlob);
                stream.getTracks().forEach(track => track.stop());
                this.isRecording = false;
                this.updateStatus('Stopped recording', 'info');
            };
            
            this.mediaRecorder.start(250);
            this.isRecording = true;
            this.updateStatus('Recording...', 'warning');
            
            this.startVoiceActivityDetection(stream);
        } catch (err) {
            console.error('Error accessing microphone:', err);
            this.updateStatus('Microphone access denied', 'error');
        }
    }
    
    stopRecording() {
        if (this.mediaRecorder && this.isRecording) {
            this.mediaRecorder.stop();
            if (this.silenceTimer) clearTimeout(this.silenceTimer);
        }
    }
    
    startVoiceActivityDetection(stream) {
        const source = this.audioContext.createMediaStreamSource(stream);
        const analyser = this.audioContext.createAnalyser();
        source.connect(analyser);
        analyser.fftSize = 512;
        
        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);
        
        const checkSilence = () => {
            if (!this.isRecording) return;
            
            analyser.getByteFrequencyData(dataArray);
            let sum = 0;
            for(let i = 0; i < bufferLength; i++) {
                sum += dataArray[i];
            }
            const average = sum / bufferLength;
            
            if (average < 10) { 
                if (!this.silenceTimer) {
                    this.silenceTimer = setTimeout(() => {
                        this.stopRecording();
                    }, 2000);
                }
            } else {
                if (this.silenceTimer) {
                    clearTimeout(this.silenceTimer);
                    this.silenceTimer = null;
                }
            }
            
            requestAnimationFrame(checkSilence);
        };
        
        checkSilence();
    }
    
    sendAudio(blob) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(blob);
            this.showSpinner();
        }
    }
    
    sendText(text) {
        if (!text.trim()) return;
        
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'text', text: text }));
            this.addChatBubble(text, 'user');
            this.showSpinner();
        }
    }
    
    async playAudioData(blob) {
        this.hideSpinner();
        const arrayBuffer = await blob.arrayBuffer();
        try {
            const audioBuffer = await this.audioContext.decodeAudioData(arrayBuffer);
            const source = this.audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(this.audioContext.destination);
            source.start();
        } catch (e) {
            console.error('Error decoding audio:', e);
        }
    }
    
    addChatBubble(text, sender) {
        if (!this.chatContainer) return;
        
        const bubble = document.createElement('div');
        bubble.className = `chat-bubble ${sender}`;
        bubble.textContent = text;
        
        this.chatContainer.appendChild(bubble);
        this.chatContainer.scrollTop = this.chatContainer.scrollHeight;
    }
    
    updateStatus(text, level) {
        if (!this.statusContainer) return;
        this.statusContainer.textContent = text;
        this.statusContainer.className = `status-text ${level}`;
    }
    
    showSpinner() {
        const spinnerId = 'voice-spinner';
        if (!document.getElementById(spinnerId) && this.chatContainer) {
            const spinner = document.createElement('div');
            spinner.id = spinnerId;
            spinner.className = 'spinner self-start text-sm text-gray-400 mt-2';
            spinner.innerHTML = '<i>Processing...</i>';
            this.chatContainer.appendChild(spinner);
            this.chatContainer.scrollTop = this.chatContainer.scrollHeight;
        }
    }
    
    hideSpinner() {
        const spinner = document.getElementById('voice-spinner');
        if (spinner) {
            spinner.remove();
        }
    }
}
