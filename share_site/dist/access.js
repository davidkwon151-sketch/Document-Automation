'use strict';
(async()=>{
  const status=document.getElementById('access-message');
  const fragment=location.hash.slice(1);
  // The capability stays out of request URLs, referrers and browser history.
  history.replaceState(null,'',location.pathname);
  const params=new URLSearchParams(fragment);
  let token=params.has('token')?params.get('token'):fragment;
  if(!token||!/^[A-Za-z0-9_-]{32,256}$/.test(token)){
    status.textContent='유효한 초대 링크가 없습니다. 운영 담당자에게 새 초대 링크를 요청해 주세요.';
    status.classList.add('error');
    return;
  }
  try{
    const body=JSON.stringify({token});
    token='';
    const response=await fetch('/access/redeem',{method:'POST',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body});
    if(!response.ok)throw Error('invalid_invitation');
    status.textContent='초대를 확인했습니다. 보호된 작업실을 여는 중입니다.';
    location.replace('/workspace');
  }catch{
    token='';
    status.textContent='초대가 만료·취소되었거나 서버에 연결할 수 없습니다. 운영 담당자에게 새 링크와 서버 상태를 확인해 주세요.';
    status.classList.add('error');
  }
})();
